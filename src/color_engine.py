#!/usr/bin/env python3
"""
KONKR Pocket FIT Elite (SM8750P) Hardware Whitepoint & Color-Temperature Engine.

Uses CIE Daylight illuminant locus + CIE 1960 UCS perpendicular Duv tint +
Bradford Chromatic Adaptation to synthesize SM8750P Version 3 HPM 17^3 3D LUTs
and synchronized HDR10 PQ tone-mapping in <0.15s using pure Python stdlib.

SPDX-License-Identifier: GPL-3.0-only
"""

from __future__ import annotations

import json
import math
from pathlib import Path

PANEL_KEY = "ar14_1080p_144hz_video_mode_dsi_panel"

D65_XYZ = [95.045471, 100.0, 108.905029]
D65_XY = (0.312713, 0.329016)

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
M_XYZ_TO_SRGB = [
    [ 3.2404542, -1.5371385, -0.4985314],
    [-0.9692660,  1.8760108,  0.0415560],
    [ 0.0556434, -0.2040259,  1.0572252],
]
M_BRADFORD = [
    [ 0.8951000,  0.2664000, -0.1614000],
    [-0.7502000,  1.7135000,  0.0367000],
    [ 0.0389000, -0.0685000,  1.0296000],
]

# Empirically characterized KONKR Pocket FIT Elite (ar14 1080p 144Hz IPS) panel model
# Extracted from 105-patch X-Rite i1Display Plus PFS characterization + closed-loop D65 trim
M_PANEL_INV = [
    [0.02630415600317594, -0.009617400949099386, -0.004258011194125098],
    [-0.0074216583538796585, 0.016191918186994288, 0.0002399576694524367],
    [0.000335147614191569, -0.0009162843164140454, 0.010104804747280337],
]
TRC_X = [
    0.0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45,
    0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0,
]
TRC_Y = [
    [0.0, 0.001572572612152006, 0.006944686165298147, 0.01656321427655425, 0.03185432567523121,
     0.05263885012957988, 0.07950876133549169, 0.10992455813726922, 0.1462664069198803, 0.19115716320186404,
     0.24198316424096256, 0.29044675296947836, 0.34945773894065596, 0.4163062085832794, 0.48573598365839443,
     0.5546427199736832, 0.6354188760629809, 0.7207428319220246, 0.8120776677943148, 0.8988115191299344, 1.0],
    [0.0, 0.0014816142618654141, 0.006464337472784491, 0.015355609196507358, 0.02936445395261882,
     0.04825284269850711, 0.0738534644686736, 0.10142579258109391, 0.13699032164752933, 0.17816507556290276,
     0.22762638968372215, 0.27301619261782656, 0.3309073594218674, 0.39598843132978234, 0.46651219893121687,
     0.5346992983050465, 0.6166933138968583, 0.7029678351234296, 0.7963873182229085, 0.8845158462959332, 1.0],
    [0.0, 0.0013863468678655597, 0.006423305166584415, 0.01559285646161247, 0.03022371877713605,
     0.05014589992734952, 0.07680292810710065, 0.10582681168986202, 0.14278687007245067, 0.1861703887464145,
     0.23792992153325354, 0.2857541087662751, 0.34587427240221086, 0.4133805789851254, 0.4847574515973375,
     0.5552117092038305, 0.6378958777525486, 0.7239803702965653, 0.8164266841041884, 0.9024193205000212, 1.0],
]
TRC_Y_LUM = [
    0.0, 0.0014937201332274228, 0.006560857255371403, 0.015622269553086026, 0.029940363776852864,
    0.0492940908904952, 0.0752319128771213, 0.10349510479917325, 0.13931893289659555, 0.1814185359122043,
    0.2313243762645712, 0.27752202663997283, 0.33580215939856783, 0.4014200613603157, 0.4717775822857259,
    0.5402735167279797, 0.6220641572782531, 0.7081285457100704, 0.8010479514282571, 0.8887371951115369, 1.0,
]

# Reference calibrated D65 P3 headroom scale & HDR10 baseline (aging-compensated)
REF_P3_SCALE_D65 = 0.9306071196
REF_HDR_PEAK = 447.0
REF_HDR_KNEE = 280.0
REF_HDR_MIN = 0.37


# ---------- Matrix & PCHIP Helpers ----------
def _mat_vec(M, v):
    return [M[0][0]*v[0] + M[0][1]*v[1] + M[0][2]*v[2],
            M[1][0]*v[0] + M[1][1]*v[1] + M[1][2]*v[2],
            M[2][0]*v[0] + M[2][1]*v[1] + M[2][2]*v[2]]


def _mat_mul(A, B):
    return [[sum(A[i][k] * B[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _mat_inv(M):
    a, b, c = M[0]
    d, e, f = M[1]
    g, h, i = M[2]
    det = a*(e*i - f*h) - b*(d*i - f*g) + c*(d*h - e*g)
    return [
        [(e*i - f*h)/det, (c*h - b*i)/det, (b*f - c*e)/det],
        [(f*g - d*i)/det, (a*i - c*g)/det, (c*d - a*f)/det],
        [(d*h - e*g)/det, (b*g - a*h)/det, (a*e - b*d)/det],
    ]


M_BRADFORD_INV = _mat_inv(M_BRADFORD)


class _PchipCurve:
    """Precomputed monotonic cubic Hermite spline for fast repeated evaluation."""
    __slots__ = ("xs", "ys", "h", "m", "n")

    def __init__(self, xs: list[float], ys: list[float]) -> None:
        self.xs = xs
        self.ys = ys
        n = len(xs)
        self.n = n
        h = [xs[i + 1] - xs[i] for i in range(n - 1)]
        d = [(ys[i + 1] - ys[i]) / h[i] for i in range(n - 1)]
        m = [0.0] * n
        m[0] = d[0]
        m[-1] = d[-1]
        for i in range(1, n - 1):
            if d[i - 1] * d[i] <= 0.0:
                m[i] = 0.0
            else:
                w1 = 2.0 * h[i] + h[i - 1]
                w2 = h[i] + 2.0 * h[i - 1]
                m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])
        if m[0] * d[0] <= 0.0:
            m[0] = 0.0
        elif abs(m[0]) > 3.0 * abs(d[0]):
            m[0] = 3.0 * d[0]
        if m[-1] * d[-1] <= 0.0:
            m[-1] = 0.0
        elif abs(m[-1]) > 3.0 * abs(d[-1]):
            m[-1] = 3.0 * d[-1]
        self.h = h
        self.m = m

    def eval(self, x: float) -> float:
        xs, ys, h, m = self.xs, self.ys, self.h, self.m
        if x <= xs[0]:
            return ys[0]
        if x >= xs[-1]:
            return ys[-1]
        lo, hi = 0, self.n - 2
        while lo < hi:
            mid = (lo + hi) >> 1
            if xs[mid + 1] < x:
                lo = mid + 1
            else:
                hi = mid
        t = (x - xs[lo]) / h[lo]
        t2 = t * t
        t3 = t2 * t
        return (
            (2.0 * t3 - 3.0 * t2 + 1.0) * ys[lo]
            + (t3 - 2.0 * t2 + t) * h[lo] * m[lo]
            + (-2.0 * t3 + 3.0 * t2) * ys[lo + 1]
            + (t3 - t2) * h[lo] * m[lo + 1]
        )


_INV_TRC = [_PchipCurve(TRC_Y[c], TRC_X) for c in range(3)]
_FWD_LUM = _PchipCurve(TRC_X, TRC_Y_LUM)


# ---------- CIE Daylight + Tint + Bradford Chromatic Adaptation ----------
def _daylight_xy(cct: float) -> tuple[float, float]:
    """CIE D-series daylight chromaticity (anchored so cct=6500 hits exact D65)."""
    T = max(4000.0, min(25000.0, float(cct))) * (6504.0 / 6500.0)
    if T <= 7000.0:
        x = -4.6070e9 / (T**3) + 2.9678e6 / (T**2) + 0.09911e3 / T + 0.244063
    else:
        x = -2.0064e9 / (T**3) + 1.9018e6 / (T**2) + 0.24748e3 / T + 0.237040
    y = -3.000 * x * x + 2.870 * x - 0.275
    return x, y


def _xy_to_uv60(x: float, y: float) -> tuple[float, float]:
    d = -2.0 * x + 12.0 * y + 3.0
    return (4.0 * x / d, 6.0 * y / d)


def _uv60_to_xy(u: float, v: float) -> tuple[float, float]:
    d = 2.0 * u - 8.0 * v + 4.0
    return (3.0 * u / d, 2.0 * v / d)


# Exact tiny offset in CIE 1960 UCS so (6500K, tint=0) lands strictly on CIE D65 (0.312713, 0.329016)
_U65_RAW, _V65_RAW = _xy_to_uv60(*_daylight_xy(6500.0))
_U65_EXACT, _V65_EXACT = _xy_to_uv60(*D65_XY)
_DU_ANCHOR = _U65_EXACT - _U65_RAW
_DV_ANCHOR = _V65_EXACT - _V65_RAW


def target_whitepoint_xyz(cct: int, tint: int = 0) -> tuple[list[float], tuple[float, float]]:
    """Return (XYZ [Y=100], (x, y)) for given CCT (5500..7500K) and Tint (-10..+10; +green, -magenta)."""
    if cct == 6500 and tint == 0:
        return list(D65_XYZ), D65_XY

    x0, y0 = _daylight_xy(cct)
    u0, v0 = _xy_to_uv60(x0, y0)
    u0 += _DU_ANCHOR
    v0 += _DV_ANCHOR

    if tint != 0:
        # Local perpendicular to daylight locus in CIE 1960 UCS, oriented toward +green (dv > 0)
        u_m, v_m = _xy_to_uv60(*_daylight_xy(cct - 25.0))
        u_p, v_p = _xy_to_uv60(*_daylight_xy(cct + 25.0))
        tu, tv = u_p - u_m, v_p - v_m
        nu, nv = -tv, tu
        if nv < 0.0:
            nu, nv = -nu, -nv
        norm = math.hypot(nu, nv) or 1.0
        step = tint * 0.0005  # +-10 -> +-0.0050 Duv
        u0 += (nu / norm) * step
        v0 += (nv / norm) * step

    xt, yt = _uv60_to_xy(u0, v0)
    xyz = [100.0 * xt / yt, 100.0, 100.0 * (1.0 - xt - yt) / yt]
    return xyz, (xt, yt)


def bradford_adapt_matrix(w_src: list[float], w_dst: list[float]) -> list[list[float]]:
    c_src = _mat_vec(M_BRADFORD, w_src)
    c_dst = _mat_vec(M_BRADFORD, w_dst)
    diag = [
        [c_dst[0] / c_src[0], 0.0, 0.0],
        [0.0, c_dst[1] / c_src[1], 0.0],
        [0.0, 0.0, c_dst[2] / c_src[2]],
    ]
    return _mat_mul(M_BRADFORD_INV, _mat_mul(diag, M_BRADFORD))


def _compute_scaled_map(M_target: list[list[float]]) -> tuple[list[list[float]], float]:
    M_raw = _mat_mul(M_PANEL_INV, M_target)
    max_val = 0.0
    for r in (0.0, 1.0):
        for g in (0.0, 1.0):
            for b in (0.0, 1.0):
                out = _mat_vec(M_raw, [r, g, b])
                m = max(out)
                if m > max_val:
                    max_val = m
    scale = 1.0 / max_val
    return [[M_raw[i][j] * scale for j in range(3)] for i in range(3)], scale


def estimate_whitepoint_preview(cct: int, tint: int = 0) -> dict:
    """Fast (<0.1ms) preview for live GUI slider updates: swatch hex, brightness retention, xy."""
    w_xyz, xy = target_whitepoint_xyz(cct, tint)
    M_adapt = bradford_adapt_matrix(D65_XYZ, w_xyz)
    M_srgb_t = _mat_mul(M_adapt, M_SRGB)
    _, scale_srgb = _compute_scaled_map(M_srgb_t)
    M_p3_t = _mat_mul(M_adapt, M_P3)
    _, scale_p3 = _compute_scaled_map(M_p3_t)

    # Convert target whitepoint XYZ to visually accurate sRGB preview swatch (normalized to max=255)
    lin_rgb = _mat_vec(M_XYZ_TO_SRGB, [v / 100.0 for v in w_xyz])
    lin_rgb = [max(0.0, c) for c in lin_rgb]
    mx = max(lin_rgb) or 1.0
    lin_rgb = [c / mx for c in lin_rgb]

    def enc_srgb(v: float) -> int:
        s = 12.92 * v if v <= 0.0031308 else 1.055 * (v ** (1.0 / 2.4)) - 0.055
        return max(0, min(255, int(round(s * 255.0))))

    swatch = f"#{enc_srgb(lin_rgb[0]):02X}{enc_srgb(lin_rgb[1]):02X}{enc_srgb(lin_rgb[2]):02X}"
    return {
        "swatch": swatch,
        "xy": xy,
        "scale_srgb": scale_srgb,
        "scale_p3": scale_p3,
        "brightness_pct": int(round(scale_srgb * 100.0)),
    }


# ---------- QDCM V3 Hex Codec & HDR10 Blob ----------
def _dec_raw(h: str, *, object_pairs_hook=None) -> dict:
    b = bytearray.fromhex(h)
    st = len(b) % 2
    for i in range(st, len(b) - 1, 2):
        b[i], b[i + 1] = b[i + 1], b[i]
    return json.loads(b.decode("utf-8"), object_pairs_hook=object_pairs_hook)


def validate_qdcm(raw: bytes) -> None:
    """Validate the V3 panel profile and every curve referenced by its four modes."""
    def unique_object(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError("duplicate JSON field")
        return result

    def unpack(encoded):
        return _dec_raw(encoded, object_pairs_hook=unique_object)

    try:
        doc = json.loads(raw, object_pairs_hook=unique_object)
        if doc["Version"] != 3:
            raise ValueError("expected QDCM Version 3")
        panel = doc[PANEL_KEY]
        for name, size, limit in (("PostBlendIGC", 385, 65535), ("PostBlendGC", 1280, 1023),
                                  ("PostBlendGamut", 4913, 4095)):
            block = unpack(doc["Common"][name])
            if block["numEntries"] != size:
                raise ValueError(f"{name}: wrong common entry count")
            checked = set()
            for mode in ("sRGB", "P3", "HDR", "Native"):
                binding = unpack(panel[mode][name])
                if binding["enable"] is not True or binding["numEntries"] != size:
                    raise ValueError(f"{mode}/{name}: disabled or wrong entry count")
                if name == "PostBlendGamut":
                    slot = binding["Custom"]
                    if type(slot) is not int or slot < 0:
                        raise ValueError(f"{mode}/{name}: invalid table ID")
                    group, key, count = "Custom", str(slot), size * 3
                else:
                    if binding["HPM"] is not True:
                        raise ValueError(f"{mode}/{name}: HPM is disabled")
                    group, key, count = "Predefined", binding["Predefined"], size
                if key in checked:
                    continue
                samples = block[group][key].removesuffix(",").split(",")
                if len(samples) != count or any(not n.isascii() or not n.isdecimal() or int(n) > limit for n in samples):
                    raise ValueError(f"{mode}/{name}: invalid LUT length or sample")
                checked.add(key)
        hdr = unpack(panel["HDR"]["PostBlendHdrBlob"])["blob"]
        if not isinstance(hdr, str) or not hdr:
            raise ValueError("missing HDR configuration")
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid Fit 8E QDCM profile: {exc}") from exc


def _enc_obj(obj: dict) -> str:
    s = json.dumps(obj, separators=(",", ":"), sort_keys=True)
    b = bytearray(s.encode("utf-8"))
    st = len(b) % 2
    for i in range(st, len(b) - 1, 2):
        b[i], b[i + 1] = b[i + 1], b[i]
    return b.hex().upper()


def _pq_eotf(e: float) -> float:
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


def _build_hdr_blob(L_max: float, L_knee: float, L_min: float) -> dict:
    xs = [0, 62, 123, 185, 254, 308, 386, 451, 482, 513, 544, 575, 606, 637, 668, 800, 923]
    L_top = _pq_eotf(xs[-1] / 1023.0)
    pts = []
    for x in xs:
        L = _pq_eotf(x / 1023.0)
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


def _build_gamut_csv(M_map: list[list[float]]) -> str:
    inv0, inv1, inv2 = _INV_TRC[0].eval, _INV_TRC[1].eval, _INV_TRC[2].eval
    fwd = _FWD_LUM.eval
    m00, m01, m02 = M_map[0]
    m10, m11, m12 = M_map[1]
    m20, m21, m22 = M_map[2]
    vals: list[str] = []
    append = vals.append
    for b_i in range(17):
        b = b_i / 16.0
        b0, b1, b2 = m02 * b, m12 * b, m22 * b
        for g_i in range(17):
            g = g_i / 16.0
            bg0 = b0 + m01 * g
            bg1 = b1 + m11 * g
            bg2 = b2 + m21 * g
            for r_i in range(17):
                r = r_i / 16.0
                y0 = bg0 + m00 * r
                y1 = bg1 + m10 * r
                y2 = bg2 + m20 * r
                y0 = 0.0 if y0 < 0.0 else (1.0 if y0 > 1.0 else y0)
                y1 = 0.0 if y1 < 0.0 else (1.0 if y1 > 1.0 else y1)
                y2 = 0.0 if y2 < 0.0 else (1.0 if y2 > 1.0 else y2)
                u0 = fwd(inv0(y0))
                u1 = fwd(inv1(y1))
                u2 = fwd(inv2(y2))
                u0 = 0.0 if u0 < 0.0 else (1.0 if u0 > 1.0 else u0)
                u1 = 0.0 if u1 < 0.0 else (1.0 if u1 > 1.0 else u1)
                u2 = 0.0 if u2 < 0.0 else (1.0 if u2 > 1.0 else u2)
                append(str(int(round(u0 * 4095.0))))
                append(str(int(round(u1 * 4095.0))))
                append(str(int(round(u2 * 4095.0))))
    return ",".join(vals) + ","


def synthesize_qdcm_bytes(base_json_path: Path, cct: int = 6500, tint: int = 0) -> tuple[bytes, dict]:
    """
    Generate a complete SM8750P V3 HPM QDCM JSON adapted to (cct, tint).
    When cct == 6500 and tint == 0, returns the exact reference asset bytes untouched.
    """
    cct = max(5500, min(7500, int(cct)))
    tint = max(-10, min(10, int(tint)))
    preview = estimate_whitepoint_preview(cct, tint)

    if cct == 6500 and tint == 0:
        return base_json_path.read_bytes(), preview

    w_xyz, _ = target_whitepoint_xyz(cct, tint)
    M_adapt = bradford_adapt_matrix(D65_XYZ, w_xyz)
    M_srgb_map, _ = _compute_scaled_map(_mat_mul(M_adapt, M_SRGB))
    M_p3_map, scale_p3 = _compute_scaled_map(_mat_mul(M_adapt, M_P3))

    rgb_nat = _mat_vec(M_PANEL_INV, w_xyz)
    s_nat = 1.0 / max(rgb_nat)
    M_native_map = [
        [rgb_nat[0] * s_nat, 0.0, 0.0],
        [0.0, rgb_nat[1] * s_nat, 0.0],
        [0.0, 0.0, rgb_nat[2] * s_nat],
    ]

    doc = json.loads(base_json_path.read_text(encoding="utf-8", errors="replace"), strict=False)
    common_gamut = {
        "Custom": {
            "0": _build_gamut_csv(M_native_map),
            "1": _build_gamut_csv(M_p3_map),
            "2": _build_gamut_csv(M_srgb_map),
        },
        "numEntries": 4913,
    }
    doc["Common"]["PostBlendGamut"] = _enc_obj(common_gamut)

    # Synchronize HDR10 tone-map effective peak proportionally to P3 headroom ratio
    hdr_ratio = scale_p3 / REF_P3_SCALE_D65
    hdr_peak = round(REF_HDR_PEAK * hdr_ratio, 1)
    hdr_knee = round(REF_HDR_KNEE * hdr_ratio, 1)
    doc[PANEL_KEY]["HDR"]["PostBlendHdrBlob"] = _enc_obj(_build_hdr_blob(hdr_peak, hdr_knee, REF_HDR_MIN))

    raw = json.dumps(doc, indent="\t").encode("utf-8")
    return raw, preview
