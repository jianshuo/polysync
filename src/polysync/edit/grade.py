"""Color grading + orientation helpers for the renderers.

Raw camera footage almost never renders correctly straight off the card. Two
things bite every time and are handled here:

1. **Log color.** Sony cameras (FX3/FX6) shoot S-Log3 / S-Gamut3.Cine by default
   — flat, grey, low-contrast. It MUST be converted to Rec.709 with a LUT or it
   looks broken. Check the `.XML` sidecar's `CaptureGammaEquation` (`s-log3-cine`)
   or run `ffprobe ... color_transfer`. `--log slog3` generates and applies the
   conversion LUT for you.
2. **Orientation.** Phones / vertically-mounted cameras record rotated. Some
   (FX3) write a rotation flag and ffmpeg auto-rotates; others (FX6 turned on its
   side) write NO flag and come out lying down. `--rotate cam:deg` fixes those.

Performance note baked into `segment_filter`: the LUT is applied AFTER the
downscale, not before. A 3D LUT on 4K (8 MP) is ~4x slower than on 1080p — and
the result is visually identical. Always scale, then grade.
"""
import os
import tempfile

import numpy as np


def make_slog3_709_lut(path, size=33):
    """Write a Sony S-Log3 / S-Gamut3.Cine -> Rec.709 3D LUT (.cube) to `path`."""
    def slog3_to_lin(n):  # n in [0,1] == 10-bit code value / 1023
        cv = n * 1023.0
        return np.where(
            cv >= 171.2102946929,
            (10 ** ((cv - 420.0) / 261.5)) * 0.19 - 0.01,
            (cv - 95.0) * 0.01125000 / (171.2102946929 - 95.0),
        )
    # S-Gamut3.Cine -> Rec.709 (linear) matrix, D65
    M = np.array([[1.6269, -0.3576, -0.2693],
                  [-0.0928, 1.3478, -0.2550],
                  [0.0387, -0.1622, 1.1235]])
    def oetf709(L):
        L = np.clip(L, 0, 1)
        return np.where(L < 0.018, 4.5 * L, 1.099 * np.power(L, 0.45) - 0.099)
    lines = ["TITLE \"SLog3 SGamut3Cine to Rec709\"", f"LUT_3D_SIZE {size}",
             "DOMAIN_MIN 0 0 0", "DOMAIN_MAX 1 1 1"]
    for b in range(size):
        for g in range(size):
            for r in range(size):
                lin = slog3_to_lin(np.array([r, g, b]) / (size - 1))
                out = oetf709(M @ lin)
                lines.append("%.6f %.6f %.6f" % (out[0], out[1], out[2]))
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return path


# Built-in log profiles -> on-the-fly LUT generators. Cached in tempdir so
# repeated render calls in one session don't regenerate.
_BUILTIN = {"slog3": make_slog3_709_lut}


def resolve_lut(lut=None, log=None):
    """Return a .cube path: explicit `lut` file wins; else generate from `log`."""
    if lut:
        return lut
    if not log:
        return None
    key = log.lower()
    if key not in _BUILTIN:
        raise SystemExit("unknown --log %r (known: %s)" % (log, ", ".join(_BUILTIN)))
    cache = os.path.join(tempfile.gettempdir(), "polysync_%s_709.cube" % key)
    if not os.path.exists(cache):
        _BUILTIN[key](cache)
    return cache


def parse_rotate(values):
    """Parse repeatable `--rotate cam:deg` into {cam_index: degrees}. Degrees in
    {90, 180, 270, -90}. 90 = clockwise."""
    out = {}
    for v in (values or []):
        cam, _, deg = v.partition(":")
        out[int(cam)] = int(deg)
    return out


def _transpose_chain(deg):
    """ffmpeg filter fragment to rotate `deg` clockwise (90/180/270/-90)."""
    deg = deg % 360
    if deg == 90:
        return "transpose=1,"
    if deg == 270:
        return "transpose=2,"
    if deg == 180:
        return "transpose=1,transpose=1,"
    return ""


def segment_filter(cam, start, end, idx, W, H, fps, rotate_deg=0, lut=None,
                   pip=False):
    """Build one segment's video filter chain. Order: trim -> rotate -> scale ->
    crop/pad -> LUT (after downscale, for speed) -> sar -> fps. With `pip=True`
    the frame fills (crop) instead of pad — used for main/inset tiles."""
    rot = _transpose_chain(rotate_deg)
    if pip:
        fit = ("scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d"
               % (W, H, W, H))
    else:
        fit = ("scale=%d:%d:force_original_aspect_ratio=decrease,"
               "pad=%d:%d:(ow-iw)/2:(oh-ih)/2" % (W, H, W, H))
    grade = ("lut3d=%s," % lut) if lut else ""
    return ("[%d:v]trim=start=%s:end=%s,setpts=PTS-STARTPTS,%s%s,%ssetsar=1,"
            "fps=%d[v%d]" % (cam, start, end, rot, fit, grade, fps, idx))
