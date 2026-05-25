"""Independent residual check for a (reference, source, sidecar) triple.

Re-extracts audio from BOTH originals NATIVELY (loudest stream, no ffmpeg
offset) and runs multi-probe cross-correlation inside the overlap window,
applying the sidecar's `delta_seconds` (and, with `apply_drift`, the slope) as
index arithmetic in numpy.

Why index arithmetic and not `ffmpeg -itsoffset`: `-itsoffset` shifts input
timestamps, but a headerless raw stream (`-f s16le`) has no timestamps to carry
the offset — ffmpeg silently drops it and inserts NO leading silence. Relying
on it lines the source's t=0 up with the reference's t=0 regardless of delta,
so every probe correlates the wrong region, peaks land in noise (ncoef ~0), and
verification falsely FAILs. Shifting indices ourselves matches exactly how the
offset was computed.

PASS = |median_residual_ms| < 15 AND residual_spread_ms < 1 frame at target fps.
A spread-only fail with a near-zero median is usually far-field-mic noise on a
wide/B-roll camera, not real desync — for camera-cut editing, trust the median.
"""
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
from scipy import signal

from . import audio

SR = 8000


def verify_files(reference, source, sidecar, probe_len=60.0, step=600.0,
                 max_frame_ms=33.33, apply_drift=False, verbose=True):
    """Run verification and write results into the sidecar's `verification`
    field. Returns (passed: bool, stats: dict)."""
    reference, source, sidecar = Path(reference), Path(source), Path(sidecar)
    sc = json.loads(sidecar.read_text())
    delta = float(sc["delta_seconds"])
    drift_slope = float(sc.get("drift_slope", 0.0))
    overlap_ref = sc["overlap_in_reference"]

    if verbose:
        print("Reference: %s" % reference.name)
        print("Source:    %s" % source.name)
        print("delta_seconds = %+.6f  drift_slope = %+.3e (%s)"
              % (delta, drift_slope,
                 "applied" if apply_drift else "not applied"))

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        ref_pcm, src_pcm = td / "ref.pcm", td / "src.pcm"
        audio.extract_pcm(reference, ref_pcm, SR)
        audio.extract_pcm(source, src_pcm, SR)
        ref = audio.read_pcm(ref_pcm)
        src = audio.read_pcm(src_pcm)

    pl = int(probe_len * SR)
    pad = int(0.5 * SR)
    ovl_start = max(60.0, float(overlap_ref[0]) + 1.0)
    ovl_end = float(overlap_ref[1]) - probe_len - 1.0
    if ovl_end <= ovl_start:
        raise ValueError("overlap window too short to verify")

    rs = []
    for bs in np.arange(ovl_start, ovl_end, step):
        # Source-local time corresponding to this reference time.
        src_t = bs - delta
        if apply_drift:
            src_t = src_t / (1.0 + drift_slope)
        si = int(src_t * SR)
        bsi = int(bs * SR)
        if si < 0 or si + pl > len(src):
            continue
        probe = src[si:si + pl]
        if np.abs(probe).mean() < 1.0:
            continue  # silence — nothing to correlate
        lo = max(0, bsi - pad)
        hi = min(len(ref), bsi + pl + pad)
        if hi - lo < pl:
            continue
        seg = ref[lo:hi].astype(np.float32)
        xc = signal.correlate(audio.normalize(seg),
                              audio.normalize(probe.astype(np.float32)),
                              mode="valid", method="fft")
        pk = int(np.argmax(np.abs(xc)))
        ncoef = float(xc[pk] / len(probe))
        residual_ms = ((lo + pk) / SR - bs) * 1000
        rs.append((bs, residual_ms, ncoef))
        if verbose:
            print("t=%7.1fs  residual=%+7.2f ms  ncoef=%+.3f"
                  % (bs, residual_ms, ncoef))

    if not rs:
        raise ValueError("no usable probes (all silence or out of overlap)")

    arr = np.array([r[1] for r in rs])
    median_residual_ms = float(np.median(arr))
    residual_spread_ms = float(np.max(np.abs(arr - median_residual_ms)) * 2)
    passed = (abs(median_residual_ms) <= 15.0
              and residual_spread_ms <= max_frame_ms)

    stats = {
        "median_residual_ms": round(median_residual_ms, 3),
        "residual_spread_ms": round(residual_spread_ms, 3),
        "probe_count": len(rs),
        "drift_applied": bool(apply_drift),
    }
    sc["verification"] = stats
    sidecar.write_text(json.dumps(sc, indent=2, ensure_ascii=False))
    if verbose:
        print("\nResidual: median=%+.2f ms  spread=+-%.2f ms  -> %s"
              % (median_residual_ms, residual_spread_ms / 2,
                 "PASS" if passed else "FAIL"))
        if not passed and abs(median_residual_ms) <= 15.0:
            print("  (spread-only fail with near-zero median is usually "
                  "far-field-mic noise, not real desync)", file=sys.stderr)
    return passed, stats
