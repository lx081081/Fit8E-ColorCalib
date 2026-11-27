#!/usr/bin/env python3
"""
KONKR Pocket FIT Elite (SM8750P) Hardware Whitepoint & Color-Temperature Engine.

Integrates CIE D-series daylight against the CIE 2015 2-degree observer.
Fits the panel in measured XYZ and adapts the sRGB/P3 content spaces in their
original 1931 coordinates before compiling the Qualcomm 17-cubed gamut LUTs.

SPDX-License-Identifier: GPL-3.0-only
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

from build_qdcm_v3 import (
    M_P3,
    M_SRGB,
    characterize_panel,
    load_ti3,
    transform_p3_enhance_to_p3 as _transform_p3_enhance_to_p3,
    transform_srgb_enhance_to_p3 as _transform_srgb_enhance_to_p3,
)

PANEL_KEY = "ar14_1080p_144hz_video_mode_dsi_panel"
D65_XY = (0.3134524493261819, 0.33080182802444436)
D65_XYZ = [100 * D65_XY[0] / D65_XY[1], 100.0, 100 * (1 - sum(D65_XY)) / D65_XY[1]]
D65_PROFILE_SHA256 = "18987308cfb4602159ee0a2f46bed55079663a3062ef6ef35a30c7f8db5beb9f"

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

# Sums of x-bar, y-bar and z-bar (rows) times daylight S0, S1 and S2 (columns).
# CIE 015:2018 daylight bases (5 nm, linearly interpolated to 1 nm) and
# CIE 170-2:2015 Table 10.7a (390–830 nm). See THIRD_PARTY_NOTICES.md.
DAYLIGHT_XYZ_2015 = (
    (10799.173028368126, 184.73368922490093, 231.90307689555266),
    (11302.081335296729, 251.19841679628666, 83.03111230838807),
    (12975.9040413579, 3822.712163524851, -239.2938400491935),
)

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


# Frozen executables carry the same two factory captures as the source checkout.
_DATA_ROOT = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
_MEASUREMENTS = _DATA_ROOT / "assets" / "measurements"
_PANEL = characterize_panel(load_ti3(_MEASUREMENTS / "factory2015-normalized.ti3"))
_PANEL_1931 = characterize_panel(load_ti3(_MEASUREMENTS / "factory1931.ti3"))


def _relative_matrix(response):
    return [[value * 100 / response.white[1] for value in row] for row in response.matrix]


M_PANEL_INV = _mat_inv(_relative_matrix(_PANEL))
M_1931_TO_2015 = _mat_mul(_relative_matrix(_PANEL), _mat_inv(_relative_matrix(_PANEL_1931)))
M_2015_TO_1931 = _mat_inv(M_1931_TO_2015)
_INV_TRC = [_PchipCurve(curve, _PANEL.levels) for curve in _PANEL.channel_curves]
_FWD_LUM = _PchipCurve(_PANEL.levels, _PANEL.luminance_curve)


def _daylight_xy(cct: float) -> tuple[float, float]:
    """Nominal D-series illuminant, integrated in the CIE 2015 2° observer."""
    temperature = float(cct) * (1.4388 / 1.4380)
    if temperature <= 7000.0:
        x = -4.6070e9 / temperature**3 + 2.9678e6 / temperature**2 + 99.11 / temperature + 0.244063
    else:
        x = -2.0064e9 / temperature**3 + 1.9018e6 / temperature**2 + 247.48 / temperature + 0.237040
    y = -3.0*x*x + 2.870*x - 0.275
    denominator = 0.0241 + 0.2562*x - 0.7341*y
    m1 = round((-1.3515 - 1.7703*x + 5.9114*y) / denominator, 3)
    m2 = round((0.0300 - 31.4424*x + 30.0717*y) / denominator, 3)
    xyz = _mat_vec(DAYLIGHT_XYZ_2015, (1.0, m1, m2))
    total = sum(xyz)
    return xyz[0] / total, xyz[1] / total


def _xy_to_uv60(x: float, y: float) -> tuple[float, float]:
    d = -2.0 * x + 12.0 * y + 3.0
    return (4.0 * x / d, 6.0 * y / d)


def _uv60_to_xy(u: float, v: float) -> tuple[float, float]:
    d = 2.0 * u - 8.0 * v + 4.0
    return (3.0 * u / d, 2.0 * v / d)


# Anchor the rounded D-series coefficients to the tabulated D65 spectrum at 6500 K.
_U65_RAW, _V65_RAW = _xy_to_uv60(*_daylight_xy(6500.0))
_U65_EXACT, _V65_EXACT = _xy_to_uv60(*D65_XY)
_DU_ANCHOR = _U65_EXACT - _U65_RAW
_DV_ANCHOR = _V65_EXACT - _V65_RAW


def target_whitepoint_xyz(cct: int, tint: int = 0) -> tuple[list[float], tuple[float, float]]:
    """Return CIE 2015 2° XYZ (Y=100) and xy for nominal daylight temperature and UCS tint."""
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


def _content_map(content_matrix, white_1931):
    # Bradford is defined for the original 1931 XYZ content space. Only then
    # change observer using the measured response of this three-primary panel.
    content_white = _mat_vec(content_matrix, (1.0, 1.0, 1.0))
    adaptation = bradford_adapt_matrix(content_white, white_1931)
    measured = _mat_mul(M_1931_TO_2015, _mat_mul(adaptation, content_matrix))
    white_y = sum(measured[1])
    return _compute_scaled_map([[v * 100 / white_y for v in row] for row in measured])


def _equivalent_1931(white_2015):
    xyz = _mat_vec(M_2015_TO_1931, white_2015)
    return [v * 100 / xyz[1] for v in xyz]


_, REF_P3_SCALE_D65 = _content_map(M_P3, _equivalent_1931(D65_XYZ))


def estimate_whitepoint_preview(cct: int, tint: int = 0) -> dict:
    """Approximate the panel white on an sRGB monitor and return its 2015 xy and headroom."""
    w_xyz, xy = target_whitepoint_xyz(cct, tint)
    white_1931 = _equivalent_1931(w_xyz)
    _, scale_srgb = _content_map(M_SRGB, white_1931)
    _, scale_p3 = _content_map(M_P3, white_1931)

    lin_rgb = _mat_vec(M_XYZ_TO_SRGB, [v / 100.0 for v in white_1931])
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


BASE_MODES = ("sRGB", "P3", "HDR", "Native")
ENHANCE_MODES = ("sRGB_Enhance", "P3_Enhance", "HDR_Enhance")


def validate_qdcm(raw: bytes) -> None:
    """Validate the V3 panel profile and every curve referenced by its active modes."""
    def unique_object(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError("duplicate JSON field")
        return result

    def unpack(encoded):
        return _dec_raw(encoded, object_pairs_hook=unique_object)

    try:
        if raw.startswith(b"\xef\xbb\xbf"):
            raise ValueError("UTF-8 BOM is not supported; save the JSON as UTF-8 without BOM")
        doc = json.loads(raw, object_pairs_hook=unique_object)
        if doc["Version"] != 3:
            raise ValueError("expected QDCM Version 3")
        panel = doc[PANEL_KEY]
        present_enhance = [m for m in ENHANCE_MODES if m in panel]
        if present_enhance and len(present_enhance) != len(ENHANCE_MODES):
            raise ValueError("incomplete Enhance mode set (expected sRGB_Enhance, P3_Enhance, HDR_Enhance)")
        modes = BASE_MODES + (ENHANCE_MODES if present_enhance else ())
        for m in present_enhance:
            if panel[m]["Applicability"]["RenderIntent"] != 2:
                raise ValueError(f"{m}: expected RenderIntent 2 (Enhance)")
        for name, size, limit in (("PostBlendIGC", 385, 65535), ("PostBlendGC", 1280, 1023),
                                  ("PostBlendGamut", 4913, 4095)):
            block = unpack(doc["Common"][name])
            if block["numEntries"] != size:
                raise ValueError(f"{name}: wrong common entry count")
            checked = set()
            for mode in modes:
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
        hdr_modes = ("HDR", "HDR_Enhance") if present_enhance else ("HDR",)
        for hm in hdr_modes:
            hdr = unpack(panel[hm]["PostBlendHdrBlob"])["blob"]
            if not isinstance(hdr, str) or not hdr:
                raise ValueError(f"{hm}: missing HDR configuration")
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


def _build_adaptive_gamut_csv(M_p3_map: list[list[float]], transform_fn) -> str:
    inv0, inv1, inv2 = _INV_TRC[0].eval, _INV_TRC[1].eval, _INV_TRC[2].eval
    fwd = _FWD_LUM.eval
    m00, m01, m02 = M_p3_map[0]
    m10, m11, m12 = M_p3_map[1]
    m20, m21, m22 = M_p3_map[2]
    vals: list[str] = []
    append = vals.append
    for b_i in range(17):
        b = b_i / 16.0
        for g_i in range(17):
            g = g_i / 16.0
            for r_i in range(17):
                r = r_i / 16.0
                pr, pg, pb = transform_fn((r, g, b))
                y0 = m00 * pr + m01 * pg + m02 * pb
                y1 = m10 * pr + m11 * pg + m12 * pb
                y2 = m20 * pr + m21 * pg + m22 * pb
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
    white_1931 = _equivalent_1931(w_xyz)
    M_srgb_map, _ = _content_map(M_SRGB, white_1931)
    M_p3_map, scale_p3 = _content_map(M_P3, white_1931)

    rgb_nat = _mat_vec(M_PANEL_INV, w_xyz)
    s_nat = 1.0 / max(rgb_nat)
    M_native_map = [
        [rgb_nat[0] * s_nat, 0.0, 0.0],
        [0.0, rgb_nat[1] * s_nat, 0.0],
        [0.0, 0.0, rgb_nat[2] * s_nat],
    ]

    doc = json.loads(base_json_path.read_text(encoding="utf-8", errors="replace"), strict=False)
    panel = doc[PANEL_KEY]
    has_enhance = all(m in panel for m in ENHANCE_MODES)
    custom_luts = {
        "0": _build_gamut_csv(M_native_map),
        "1": _build_gamut_csv(M_p3_map),
        "2": _build_gamut_csv(M_srgb_map),
    }
    if has_enhance:
        custom_luts["3"] = _build_adaptive_gamut_csv(M_p3_map, _transform_p3_enhance_to_p3)
        custom_luts["4"] = _build_adaptive_gamut_csv(M_p3_map, _transform_srgb_enhance_to_p3)
    common_gamut = {
        "Custom": custom_luts,
        "numEntries": 4913,
    }
    doc["Common"]["PostBlendGamut"] = _enc_obj(common_gamut)

    # Synchronize HDR10 tone-map effective peak proportionally to P3 headroom ratio
    hdr_ratio = scale_p3 / REF_P3_SCALE_D65
    hdr_peak = round(REF_HDR_PEAK * hdr_ratio, 1)
    hdr_knee = round(REF_HDR_KNEE * hdr_ratio, 1)
    hdr_blob_enc = _enc_obj(_build_hdr_blob(hdr_peak, hdr_knee, REF_HDR_MIN))
    panel["HDR"]["PostBlendHdrBlob"] = hdr_blob_enc
    if has_enhance:
        panel["HDR_Enhance"]["PostBlendHdrBlob"] = hdr_blob_enc

    raw = json.dumps(doc, indent="\t").encode("utf-8")
    return raw, preview
