#!/usr/bin/env python3
"""
SM8750P (Snapdragon 8 Elite / DPU 12.x) Version 3 HPM QDCM Calibration Generator.

Converts an ArgyllCMS native (uncalibrated) panel characterization `.ti3` file into a
4-mode hardware QDCM calibration JSON (sRGB, Display P3, HDR10 PQ, Native D65) using
pure Python standard library (zero third-party dependencies).

Hardware pipeline targeted (DPU 12.x Version 3 HPM):
  1. PostBlendIGC:   385-entry 16-bit HPM LUT (257 main + 128 4x-shadow entries)
  2. PostBlendGamut: 17x17x17 (4913-entry) 12-bit 3D LUT with per-channel panel TRC
                     pre-compensation mapped onto the shared luminance GC domain
  3. PostBlendGC:    1280-entry 10-bit HPM LUT (1024 main + 256 64x-shadow entries)
  4. PostBlendHdrBlob: Custom IPS LCD ST.2084 PQ tone-map curve (1:1 up to knee + smooth roll-off)

License: MIT
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import shlex
import sys
from pathlib import Path

D65 = [95.045471, 100.0, 108.905029]
M_SRGB = [
    [41.24564, 35.75761, 18.04375],
    [21.26729, 71.51522,  7.21750],
    [ 1.93339, 11.91920, 95.03041],
]
M_P3 = [
    [48.6570949, 26.5667693, 19.8217285],
    [22.8974564, 69.1738522,  7.9286914],
    [ 0.0000000,  4.5113382, 104.3944369],
]


# ---------- Linear Algebra & Color Math ----------
def mat_vec(M, v):
    return [sum(M[i][j] * v[j] for j in range(3)) for i in range(3)]


def mat_mul(A, B):
    return [[sum(A[i][k] * B[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def mat_inv(M):
    a, b, c = M[0]
    d, e, f = M[1]
    g, h, i = M[2]
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    return [
        [(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det],
        [(f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det],
        [(d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det],
    ]


def srgb_eotf(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4


def pq_eotf(e: float) -> float:
    """SMPTE ST 2084 PQ EOTF: code in [0..1] -> nits in [0..10000]."""
    e = max(0.0, min(1.0, e))
    m1 = 2610.0 / 16384.0
    m2 = 2523.0 / 32.0
    c1 = 3424.0 / 4096.0
    c2 = 2413.0 / 128.0
    c3 = 2392.0 / 128.0
    em = e ** (1.0 / m2)
    num = max(0.0, em - c1)
    den = max(1e-12, c2 - c3 * em)
    return 10000.0 * ((num / den) ** (1.0 / m1))


def pchip_interp(xs: list[float], ys: list[float], xq: list[float]) -> list[float]:
    """Monotonic Fritsch-Carlson cubic Hermite interpolation."""
    n = len(xs)
    h = [xs[i + 1] - xs[i] for i in range(n - 1)]
    d = [(ys[i + 1] - ys[i]) / h[i] for i in range(n - 1)]
    m = [0.0] * n
    m[0] = d[0]
    m[-1] = d[-1]
    for i in range(1, n - 1):
        if d[i - 1] * d[i] <= 0:
            m[i] = 0.0
        else:
            w1 = 2 * h[i] + h[i - 1]
            w2 = h[i] + 2 * h[i - 1]
            m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])
    if m[0] * d[0] <= 0:
        m[0] = 0.0
    elif abs(m[0]) > 3 * abs(d[0]):
        m[0] = 3 * d[0]
    if m[-1] * d[-1] <= 0:
        m[-1] = 0.0
    elif abs(m[-1]) > 3 * abs(d[-1]):
        m[-1] = 3 * d[-1]

    out = []
    for x in xq:
        if x <= xs[0]:
            out.append(ys[0])
            continue
        if x >= xs[-1]:
            out.append(ys[-1])
            continue
        lo, hi = 0, n - 2
        while lo < hi:
            mid = (lo + hi) // 2
            if xs[mid + 1] < x:
                lo = mid + 1
            else:
                hi = mid
        i = lo
        t = (x - xs[i]) / h[i]
        t2, t3 = t * t, t * t * t
        val = (
            (2 * t3 - 3 * t2 + 1) * ys[i]
            + (t3 - 2 * t2 + t) * h[i] * m[i]
            + (-2 * t3 + 3 * t2) * ys[i + 1]
            + (t3 - t2) * h[i] * m[i + 1]
        )
        out.append(val)
    return out


def load_ti3(path: Path) -> list[list[float]]:
    """Read the first CTI3 RGB/XYZ measurement table in canonical channel order."""
    required = ("RGB_R", "RGB_G", "RGB_B", "XYZ_X", "XYZ_Y", "XYZ_Z")
    with path.open(encoding="utf-8-sig") as source:
        lines = (shlex.split(line, comments=True) for line in source)
        tokens = (parts for parts in lines if parts)
        if next(tokens, []) != ["CTI3"]:
            raise ValueError(f"{path}: expected a CTI3 measurement table")
        header = {}
        for parts in tokens:
            if parts == ["BEGIN_DATA_FORMAT"]:
                break
            if len(parts) == 2:
                header[parts[0]] = parts[1]
        if header.get("DEVICE_CLASS", "DISPLAY") != "DISPLAY":
            raise ValueError(f"{path}: expected display measurements")
        if header.get("COLOR_REP", "RGB_XYZ") != "RGB_XYZ":
            raise ValueError(f"{path}: expected RGB_XYZ measurements")
        fields = []
        for parts in tokens:
            if parts == ["END_DATA_FORMAT"]:
                break
            fields.extend(parts)
        if len(set(fields)) != len(fields) or not set(required).issubset(fields):
            raise ValueError(f"{path}: missing or duplicate RGB/XYZ fields in BEGIN_DATA_FORMAT")
        if int(header.get("NUMBER_OF_FIELDS", len(fields))) != len(fields):
            raise ValueError(f"{path}: NUMBER_OF_FIELDS does not match the field names")
        columns = [fields.index(name) for name in required]
        expected = None
        for parts in tokens:
            if parts == ["BEGIN_DATA"]:
                break
            if len(parts) == 2 and parts[0] == "NUMBER_OF_SETS":
                expected = int(parts[1])
        rows = []
        for parts in tokens:
            if parts == ["END_DATA"]:
                if not rows or (expected is not None and expected != len(rows)):
                    raise ValueError(f"{path}: unexpected number of measurement rows ({len(rows)})")
                return rows
            if len(parts) != len(fields):
                raise ValueError(f"{path}: wrong field count in measurement {len(rows) + 1}")
            values = [float(parts[index]) for index in columns]
            if not all(math.isfinite(v) for v in values) or any(v < 0 or v > 100 for v in values[:3]):
                raise ValueError(f"{path}: invalid channel value in measurement {len(rows) + 1}")
            # Downstream math uses the first slot only as a row identifier.
            rows.append([len(rows) + 1, *values])
    raise ValueError(f"{path}: incomplete CTI3 measurement table")


def avg_xyz(rows_list: list[list[float]]) -> list[float]:
    n = len(rows_list)
    return [sum(r[4 + c] for r in rows_list) / n for c in range(3)]


def xyz_to_lab(xyz, wp):
    e = 216.0 / 24389.0
    k = 24389.0 / 27.0
    f = [((xyz[i] / wp[i]) ** (1 / 3) if (xyz[i] / wp[i]) > e else (k * (xyz[i] / wp[i]) + 16) / 116.0) for i in range(3)]
    return [116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])]


def ciede2000(lab1, lab2) -> float:
    L1, a1, b1 = lab1
    L2, a2, b2 = lab2
    C1 = math.hypot(a1, b1)
    C2 = math.hypot(a2, b2)
    Cb = (C1 + C2) / 2.0
    G = 0.5 * (1 - math.sqrt(Cb**7 / (Cb**7 + 25**7)))
    a1p = (1 + G) * a1
    a2p = (1 + G) * a2
    C1p = math.hypot(a1p, b1)
    C2p = math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360 if (a1p != 0 or b1 != 0) else 0.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360 if (a2p != 0 or b2 != 0) else 0.0
    dLp = L2 - L1
    dCp = C2p - C1p
    if C1p * C2p == 0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180:
        dhp = h2p - h1p
    elif h2p - h1p > 180:
        dhp = h2p - h1p - 360
    else:
        dhp = h2p - h1p + 360
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp / 2.0))
    Lbp = (L1 + L2) / 2.0
    Cbp = (C1p + C2p) / 2.0
    if C1p * C2p == 0:
        hbp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hbp = (h1p + h2p) / 2.0
    elif h1p + h2p < 360:
        hbp = (h1p + h2p + 360) / 2.0
    else:
        hbp = (h1p + h2p - 360) / 2.0
    T = (
        1
        - 0.17 * math.cos(math.radians(hbp - 30))
        + 0.24 * math.cos(math.radians(2 * hbp))
        + 0.32 * math.cos(math.radians(3 * hbp + 6))
        - 0.20 * math.cos(math.radians(4 * hbp - 63))
    )
    dt = 30 * math.exp(-(((hbp - 275) / 25.0) ** 2))
    RC = 2 * math.sqrt(Cbp**7 / (Cbp**7 + 25**7))
    SL = 1 + (0.015 * (Lbp - 50) ** 2) / math.sqrt(20 + (Lbp - 50) ** 2)
    SC = 1 + 0.045 * Cbp
    SH = 1 + 0.015 * Cbp * T
    RT = -math.sin(math.radians(2 * dt)) * RC
    return math.sqrt((dLp / SL) ** 2 + (dCp / SC) ** 2 + (dHp / SH) ** 2 + RT * (dCp / SC) * (dHp / SH))


# ---------- QDCM V3 Hex-Byte-Swap Codec ----------
def dec_raw(h: str) -> dict:
    b = bytearray.fromhex(h)
    st = len(b) % 2
    for i in range(st, len(b) - 1, 2):
        b[i], b[i + 1] = b[i + 1], b[i]
    return json.loads(b.decode("utf-8"))


def enc_obj(obj: dict) -> str:
    s = json.dumps(obj, separators=(",", ":"), sort_keys=True)
    b = bytearray(s.encode("utf-8"))
    st = len(b) % 2
    for i in range(st, len(b) - 1, 2):
        b[i], b[i + 1] = b[i + 1], b[i]
    return b.hex().upper()


def build_hdr_blob(L_max: float, L_knee: float, L_min: float) -> dict:
    """Build custom IPS LCD PostBlendHdrBlob: 1:1 ST.2084 PQ up to L_knee, smooth roll-off to L_max."""
    xs = [0, 62, 123, 185, 254, 308, 386, 451, 482, 513, 544, 575, 606, 637, 668, 800, 923]
    L_top = pq_eotf(xs[-1] / 1023.0)
    pts = []
    for x in xs:
        L = pq_eotf(x / 1023.0)
        if x == 0:
            y = 0
        elif L <= L_knee:
            y = int(round(1023.0 * ((max(0.0, L) / L_max) ** (1.0 / 2.2))))
        else:
            u = (L - L_knee) / max(1e-6, (L_max - L_knee))
            u_top = (L_top - L_knee) / max(1e-6, (L_max - L_knee))
            L_out = L_knee + (L_max - L_knee) * (u / (1.0 + u)) / (u_top / (1.0 + u_top))
            y = int(round(1023.0 * ((min(L_max, L_out) / L_max) ** (1.0 / 2.2))))
        pts.append((x, max(0, min(1023, y))))
    pts[-1] = (923, 1023)

    print(f"HDR10 tone_map (L_max={L_max:.1f}, L_knee={L_knee:.1f}, L_min={L_min:.2f} nit):")
    for x, y in pts:
        print(f"  x={x:3d} ({pq_eotf(x/1023.0):6.1f} nit) -> y={y:4d} ({L_max*((y/1023.0)**2.2):5.1f} nit)")

    pts_xml = "".join(f"<x>{x}</x><y>{y}</y>" for x, y in pts)
    grid = "".join(f"<p>{v}</p>" for v in [0, 248, 492, 741, 1017, 1233, 1546, 1806, 1930, 2054, 2178, 2302, 2426, 2550, 2675, 3203, 3697])
    xml = (
        '<?xml version="1.0" encoding="utf-8"?><hdr_lib version="2.0"><QDCM_version>6.23</QDCM_version>'
        '<panel_color_mode>DCIP3</panel_color_mode>'
        f'<panel_max_luminance>{L_max:.4f}</panel_max_luminance>'
        '<used_APL>100</used_APL>'
        f'<panel_min_luminance>{L_min:.4f}</panel_min_luminance>'
        f'<nominal_peak_luminance>{L_max:.4f}</nominal_peak_luminance>'
        '<SDR_peak_luminance>300</SDR_peak_luminance>'
        '<output_gamma_space><space>2p2</space><val>2.2</val></output_gamma_space>'
        '<panel_pcc>'
        '<r><c>0</c><r>1</r><g>0</g><b>0</b><rg>0</rg><gb>0</gb><rb>0</rb><rr>0</rr><gg>0</gg><bb>0</bb><rgb>0</rgb></r>'
        '<g><c>0</c><r>0</r><g>1</g><b>0</b><rg>0</rg><gb>0</gb><rb>0</rb><rr>0</rr><gg>0</gg><bb>0</bb><rgb>0</rgb></g>'
        '<b><c>0</c><r>0</r><g>0</g><b>1</b><rg>0</rg><gb>0</gb><rb>0</rb><rr>0</rr><gg>0</gg><bb>0</bb><rgb>0</rgb></b>'
        '</panel_pcc>'
        f'<tone_map><points>{pts_xml}</points></tone_map>'
        f'<sampling_grid><red>{grid}</red><green>{grid}</green><blue>{grid}</blue></sampling_grid>'
        '<dspp_sampling_grid><type>xvector</type><size>17</size></dspp_sampling_grid>'
        '<realtime_panel_luminance><enable>0</enable><adapt_strat_type>default</adapt_strat_type><clamping><low>250</low><high>500</high></clamping></realtime_panel_luminance>'
        '<adaptive_tonemapper>'
        '<VideoStatisticDataConfig><enable>0</enable><rate_ctrl_type>default</rate_ctrl_type><recal_rate_control>0.5</recal_rate_control></VideoStatisticDataConfig>'
        '<StaticMetaDataConfig><enable>0</enable><mode>0</mode><weightOfMaxCLL>1</weightOfMaxCLL><weightOfMaxMasteringDisp>1</weightOfMaxMasteringDisp><weightVideoStatistics>0</weightVideoStatistics></StaticMetaDataConfig>'
        '</adaptive_tonemapper>'
        '<dynamicTM_power_optimizer><enable>0</enable><rate_ctrl_type>default</rate_ctrl_type><recal_rate_control>0.5</recal_rate_control></dynamicTM_power_optimizer>'
        '</hdr_lib>'
    )
    return {"blob": xml}


def generate_qdcm_v3(
    ti3_path: Path,
    template_path: Path,
    out_path: Path,
    panel_key: str = "ar14_1080p_144hz_video_mode_dsi_panel",
    gamma_nudge: float = 1.0,
    cl_gain: tuple[float, float, float] = (1.0, 1.0, 1.0),
    hdr_peak: float = 447.0,
    hdr_knee: float = 280.0,
    hdr_min: float = 0.37,
    verify_ti3: Path | None = None,
) -> None:
    verify_rows = load_ti3(verify_ti3) if verify_ti3 is not None else None
    rows = load_ti3(ti3_path)
    blacks = [r for r in rows if r[1] == 0 and r[2] == 0 and r[3] == 0]
    whites = [r for r in rows if abs(r[1] - 100) < 1e-3 and abs(r[2] - 100) < 1e-3 and abs(r[3] - 100) < 1e-3]
    reds   = [r for r in rows if abs(r[1] - 100) < 1e-3 and r[2] == 0 and r[3] == 0]
    greens = [r for r in rows if r[1] == 0 and abs(r[2] - 100) < 1e-3 and r[3] == 0]
    blues  = [r for r in rows if r[1] == 0 and r[2] == 0 and abs(r[3] - 100) < 1e-3]
    if not (blacks and whites and reds and greens and blues):
        raise ValueError("TI3 must contain black (0,0,0), white (100,100,100), and full R/G/B primaries.")

    K_xyz = avg_xyz(blacks)
    W_xyz = avg_xyz(whites)
    R_xyz = avg_xyz(reds)
    G_xyz = avg_xyz(greens)
    B_xyz = avg_xyz(blues)

    M_raw = [
        [R_xyz[0] - K_xyz[0], G_xyz[0] - K_xyz[0], B_xyz[0] - K_xyz[0]],
        [R_xyz[1] - K_xyz[1], G_xyz[1] - K_xyz[1], B_xyz[1] - K_xyz[1]],
        [R_xyz[2] - K_xyz[2], G_xyz[2] - K_xyz[2], B_xyz[2] - K_xyz[2]],
    ]
    W_net = [W_xyz[i] - K_xyz[i] for i in range(3)]
    s_prim = mat_vec(mat_inv(M_raw), W_net)

    # Unscaled panel matrix for clean per-channel TRC extraction
    M_panel_trc = [[M_raw[i][j] * s_prim[j] for j in range(3)] for i in range(3)]
    M_panel_trc_inv = mat_inv(M_panel_trc)
    # Apply optional closed-loop channel trim ONLY to color matrices after TRC extraction
    M_panel = [[M_raw[i][j] * s_prim[j] / cl_gain[j] for j in range(3)] for i in range(3)]
    M_panel_inv = mat_inv(M_panel)

    gray_map: dict[float, list[list[float]]] = {}
    for r in rows:
        if abs(r[1] - r[2]) < 1e-3 and abs(r[2] - r[3]) < 1e-3:
            gray_map.setdefault(round(r[1], 4), []).append(r)
    gray_lvls = sorted(gray_map.keys())
    if len(gray_lvls) < 5:
        raise ValueError(f"Expected >=5 neutral gray patches in TI3, found {len(gray_lvls)}")

    trc_x = [lvl / 100.0 for lvl in gray_lvls]
    trc_y: list[list[float]] = [[], [], []]
    trc_y_lum: list[float] = []
    for lvl in gray_lvls:
        xyz = avg_xyz(gray_map[lvl])
        net = [max(0.0, xyz[i] - K_xyz[i]) for i in range(3)]
        lin = mat_vec(M_panel_trc_inv, net)
        for c in range(3):
            trc_y[c].append(max(0.0, min(1.0, lin[c])))
        trc_y_lum.append(max(0.0, min(1.0, net[1] / W_net[1])))

    for c in range(3):
        trc_y[c][0] = 0.0
        trc_y[c][-1] = 1.0
        for i in range(1, len(trc_y[c])):
            if trc_y[c][i] <= trc_y[c][i - 1]:
                trc_y[c][i] = trc_y[c][i - 1] + 1e-6
    trc_y_lum[0] = 0.0
    trc_y_lum[-1] = 1.0
    for i in range(1, len(trc_y_lum)):
        if trc_y_lum[i] <= trc_y_lum[i - 1]:
            trc_y_lum[i] = trc_y_lum[i - 1] + 1e-6

    # 1. HPM PostBlendIGC (385 entries, 16-bit 0..65535)
    def igc_curve(x: float) -> float:
        y = srgb_eotf(x)
        return (y ** gamma_nudge) if y > 0.0 else 0.0

    igc_385 = [int(round(igc_curve(i / 256.0) * 65535.0)) for i in range(257)]
    igc_385 += [int(round(igc_curve(j / 1024.0) * 65535.0)) for j in range(128)]
    igc_385[0] = 0
    igc_385[256] = 65535
    igc_385[257] = 0

    # 2. HPM PostBlendGC (1280 entries, 10-bit 0..1023)
    gc_main_xs = [i / 1023.0 for i in range(1024)]
    gc_hpm_xs = [j / (1023.0 * 64.0) for j in range(256)]
    gc_1280 = [int(round(max(0.0, min(1.0, v)) * 1023.0)) for v in pchip_interp(trc_y_lum, trc_x, gc_main_xs)]
    gc_1280 += [int(round(max(0.0, min(1.0, v)) * 1023.0)) for v in pchip_interp(trc_y_lum, trc_x, gc_hpm_xs)]
    gc_1280[0] = 0
    gc_1280[1023] = 1023
    gc_1280[1024] = 0

    # 3. 3D Gamut LUTs (17x17x17 = 4913 entries, 12-bit 0..4095)
    def compute_matrix_and_scale(M_target, name: str):
        M_raw_map = mat_mul(M_panel_inv, M_target)
        max_val = 0.0
        for r in (0.0, 1.0):
            for g in (0.0, 1.0):
                for b in (0.0, 1.0):
                    max_val = max(max_val, max(mat_vec(M_raw_map, [r, g, b])))
        scale = 1.0 / max_val
        M_scaled = [[M_raw_map[i][j] * scale for j in range(3)] for i in range(3)]
        print(f"{name:10s}: scale={scale:.4f} (headroom loss={(1-scale)*100:.1f}%), W_lin={[round(x,4) for x in mat_vec(M_scaled,[1,1,1])]}")
        return M_scaled

    M_srgb_map = compute_matrix_and_scale(M_SRGB, "sRGB")
    M_p3_map = compute_matrix_and_scale(M_P3, "Display P3")
    rgb_d65 = mat_vec(M_panel_inv, D65)
    s_nat = 1.0 / max(rgb_d65)
    M_native_map = [
        [rgb_d65[0] * s_nat, 0.0, 0.0],
        [0.0, rgb_d65[1] * s_nat, 0.0],
        [0.0, 0.0, rgb_d65[2] * s_nat],
    ]
    print(f"{'Native D65':10s}: W_lin={[round(x,4) for x in mat_vec(M_native_map,[1,1,1])]}")

    def build_gamut_csv(M_map) -> str:
        vals = []
        for b_i in range(17):
            b = b_i / 16.0
            for g_i in range(17):
                g = g_i / 16.0
                for r_i in range(17):
                    r = r_i / 16.0
                    lin = mat_vec(M_map, [r, g, b])
                    for c in range(3):
                        yc = max(0.0, min(1.0, lin[c]))
                        vc = pchip_interp(trc_y[c], trc_x, [yc])[0]
                        uc = pchip_interp(trc_x, trc_y_lum, [vc])[0]
                        vals.append(str(int(round(max(0.0, min(1.0, uc)) * 4095.0))))
        return ",".join(vals) + ","

    gamut_native_csv = build_gamut_csv(M_native_map)
    gamut_p3_csv = build_gamut_csv(M_p3_map)
    gamut_srgb_csv = build_gamut_csv(M_srgb_map)

    # Optional digital pipeline simulation
    if verify_rows is not None:

        def sim_v3(rgb_in, gam_csv):
            nums = [int(x) for x in gam_csv.split(",") if x]
            lin_in = []
            for x in rgb_in:
                if x < 127 / 1024.0:
                    p = x * 1024.0
                    i0 = min(126, int(p))
                    f = p - i0
                    lin_in.append((igc_385[257 + i0] * (1 - f) + igc_385[257 + i0 + 1] * f) / 65535.0)
                else:
                    p = x * 256.0
                    i0 = min(255, int(p))
                    f = p - i0
                    lin_in.append((igc_385[i0] * (1 - f) + igc_385[i0 + 1] * f) / 65535.0)
            x, y, z = [max(0.0, min(1.0, c)) * 16.0 for c in lin_in]
            x0, y0, z0 = min(15, int(x)), min(15, int(y)), min(15, int(z))
            fx, fy, fz = x - x0, y - y0, z - z0
            u = [0.0, 0.0, 0.0]
            for dz in (0, 1):
                wz = fz if dz else (1 - fz)
                for dy in (0, 1):
                    wy = fy if dy else (1 - fy)
                    for dx in (0, 1):
                        wx = fx if dx else (1 - fx)
                        base = ((z0 + dz) * 289 + (y0 + dy) * 17 + (x0 + dx)) * 3
                        w = wx * wy * wz
                        for c in range(3):
                            u[c] += w * nums[base + c] / 4095.0
            v_enc = []
            for c in range(3):
                if u[c] < 255 / (1023.0 * 64.0):
                    p = u[c] * 1023.0 * 64.0
                    i0 = min(254, int(p))
                    f = p - i0
                    v_enc.append((gc_1280[1024 + i0] * (1 - f) + gc_1280[1024 + i0 + 1] * f) / 1023.0)
                else:
                    p = u[c] * 1023.0
                    i0 = min(1022, int(p))
                    f = p - i0
                    v_enc.append((gc_1280[i0] * (1 - f) + gc_1280[i0 + 1] * f) / 1023.0)
            lin_phys = [pchip_interp(trc_x, trc_y[c], [v_enc[c]])[0] for c in range(3)]
            return [mat_vec(M_panel, lin_phys)[i] + K_xyz[i] for i in range(3)]

        for name, csv_data, M_tgt in [("sRGB", gamut_srgb_csv, M_SRGB), ("Display P3", gamut_p3_csv, M_P3)]:
            w_sim = sim_v3([1, 1, 1], csv_data)
            sc = 100.0 / w_sim[1]
            des = []
            for r in verify_rows:
                rgb = [r[1] / 100.0, r[2] / 100.0, r[3] / 100.0]
                xyz_s = [v * sc for v in sim_v3(rgb, csv_data)]
                xyz_r = mat_vec(M_tgt, [srgb_eotf(c) for c in rgb])
                des.append(ciede2000(xyz_to_lab(xyz_r, D65), xyz_to_lab(xyz_s, D65)))
            print(f"Simulated {name:10s} ({len(verify_rows)} patches vs D65): Avg dE00 = {sum(des)/len(des):.3f}, Max = {max(des):.3f}")

    # 4. Load Version 3 HPM template skeleton
    ref = json.loads(template_path.read_text(encoding="utf-8", errors="replace"), strict=False)
    if ref.get("Version") != 3:
        raise ValueError(f"Template must be SM8750 QDCM Version 3, got Version={ref.get('Version')}")
    src_panel_key = next(k for k in ref.keys() if k not in ("Copyright", "Version", "Common"))
    src_panel = ref[src_panel_key]

    common_igc = {"Predefined": {"Rec601": ",".join(map(str, igc_385)) + ","}, "numEntries": 385}
    common_gc = {"Predefined": {"Rec601noDither": ",".join(map(str, gc_1280)) + ","}, "numEntries": 1280}
    common_gamut = {"Custom": {"0": gamut_native_csv, "1": gamut_p3_csv, "2": gamut_srgb_csv}, "numEntries": 4913}

    def make_v3_mode(src_mode: dict, gamut_id: int) -> dict:
        m = dict(src_mode)
        m["PostBlendIGC"] = enc_obj({
            "HPM": True, "Predefined": "Rec601", "displayID": 0,
            "ditherEnable": True, "ditherStrength": 4, "enable": True, "numEntries": 385,
        })
        m["PostBlendGamut"] = enc_obj({
            "Custom": gamut_id, "displayID": 0, "enable": True, "numEntries": 4913,
        })
        m["PostBlendGC"] = enc_obj({
            "HPM": True, "Predefined": "Rec601noDither", "bitsRounding": 10,
            "displayID": 0, "enable": True, "numEntries": 1280,
        })
        m["PostBlendPCC"] = enc_obj({"displayID": 0, "enable": False})
        dith = dec_raw(src_panel["sRGB"]["PostBlendDither"])
        dith["enable"] = True
        m["PostBlendDither"] = enc_obj(dith)
        return m

    hdr_mode = make_v3_mode(src_panel["HDR"], 1)
    hdr_mode["PostBlendHdrBlob"] = enc_obj(build_hdr_blob(hdr_peak, hdr_knee, hdr_min))

    out_json = {
        "Copyright": ref.get("Copyright", "Copyright (c) Qualcomm Technologies, Inc."),
        "Version": 3,
        "Common": {
            "PostBlendGC": enc_obj(common_gc),
            "PostBlendGamut": enc_obj(common_gamut),
            "PostBlendIGC": enc_obj(common_igc),
        },
        panel_key: {
            "sRGB": make_v3_mode(src_panel["sRGB"], 2),
            "P3": make_v3_mode(src_panel["P3"], 1),
            "HDR": hdr_mode,
            "Native": make_v3_mode(src_panel["Native"], 0),
        },
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out_json, f, indent="\t")
    print(f"\nWrote SM8750P V3 HPM QDCM JSON -> {out_path} ({out_path.stat().st_size} bytes)")


def main() -> int:
    default_tpl = Path(__file__).resolve().parent.parent / "assets" / "qdcm_calib_data_ar14_1080p_144hz_video_mode_dsi_panel.json"
    p = argparse.ArgumentParser(description="Generate SM8750P V3 HPM hardware QDCM calibration JSON from ArgyllCMS .ti3")
    p.add_argument("--ti3", type=Path, required=True, help="Input ArgyllCMS native panel characterization .ti3 file")
    p.add_argument("--template", type=Path, default=default_tpl, help="SM8750 Version 3 template JSON (defaults to repo asset)")
    p.add_argument("--out", type=Path, default=Path("qdcm_calib_data_ar14_1080p_144hz_video_mode_dsi_panel.json"), help="Output JSON path")
    p.add_argument("--panel-key", default="ar14_1080p_144hz_video_mode_dsi_panel", help="Target DSI panel key")
    p.add_argument("--gamma-nudge", type=float, default=1.0, help="Closed-loop IGC gamma exponent nudge (default: 1.0)")
    p.add_argument("--cl-gain", default="1.0,1.0,1.0", help="Closed-loop RGB whitepoint trim R,G,B (default: 1.0,1.0,1.0)")
    p.add_argument("--hdr-peak", type=float, default=447.0, help="Effective HDR10 peak luminance in nits (default: 447.0)")
    p.add_argument("--hdr-knee", type=float, default=280.0, help="HDR10 1:1 ST.2084 PQ knee in nits (default: 280.0)")
    p.add_argument("--hdr-min", type=float, default=0.37, help="Panel black floor in nits (default: 0.37)")
    p.add_argument("--verify-ti3", type=Path, default=None, help="Optional verification patch .ti3 for pipeline dE00 simulation")
    args = p.parse_args()

    try:
        gains = tuple(float(x) for x in args.cl_gain.split(","))
        if len(gains) != 3:
            raise ValueError("--cl-gain must have 3 comma-separated floats")

        generate_qdcm_v3(
            ti3_path=args.ti3,
            template_path=args.template,
            out_path=args.out,
            panel_key=args.panel_key,
            gamma_nudge=args.gamma_nudge,
            cl_gain=gains,
            hdr_peak=args.hdr_peak,
            hdr_knee=args.hdr_knee,
            hdr_min=args.hdr_min,
            verify_ti3=args.verify_ti3,
        )
    except (OSError, ValueError) as exc:
        p.exit(2, f"ERROR: {exc}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
