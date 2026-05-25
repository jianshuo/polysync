"""Compute the time offset between two recordings of the same event.

Algorithm (envelope cross-correlation + multi-probe drift fit):
  1. Log-energy envelope of each signal (audio.log_envelope), high-passed.
  2. FFT cross-correlate envelopes end-to-end -> coarse offset (~10 ms).
  3. Refine at sample level with 60 s probes near the coarse position,
     parabolic peak interpolation.
  4. Linear-fit delta(t) across probes -> clock drift; report the
     midpoint-canonical offset so residual error is symmetric around zero.

Two failure philosophies, selected by `partial`:
  - partial=False (full-overlap multicam): demand >=3 good probes or raise
    SyncError. Too few good matches almost always means the wrong files.
  - partial=True (a source covering only part of the reference): degrade
    gracefully — median delta on few probes, coarse delta if none.

`compute_sync` works on numpy PCM arrays (unit-testable, no ffmpeg). `sync_files`
is the file/CLI layer that extracts audio and writes sidecars.
"""
import sys
import tempfile
from pathlib import Path

import numpy as np
from scipy import signal

from . import audio
from .sidecar import write_sidecar

SR = 8000  # sync works fine at 8 kHz; the envelope is what matters, not HF
GOOD_NCOEF = 0.05  # a probe counts as "good" above this normalized correlation


class SyncError(Exception):
    """Raised when full-overlap sync cannot find enough evidence to trust."""


class SyncResult(object):
    """Outcome of compute_sync. `delta_seconds` is the source's t=0 expressed
    in the reference timeline; positive => source starts after reference."""

    def __init__(self, delta_seconds, drift_slope, coarse_corr,
                 n_probes, n_good, fallback):
        self.delta_seconds = float(delta_seconds)
        self.drift_slope = float(drift_slope)
        self.coarse_corr = float(coarse_corr)
        self.n_probes = int(n_probes)
        self.n_good = int(n_good)
        self.fallback = fallback  # None | "median" | "coarse"

    def __repr__(self):
        return ("SyncResult(delta=%.6f, drift=%.3e, coarse_corr=%.3f, "
                "probes=%d, good=%d, fallback=%r)"
                % (self.delta_seconds, self.drift_slope, self.coarse_corr,
                   self.n_probes, self.n_good, self.fallback))


def coarse_offset(env_a, env_b, env_sr):
    """Return (delta, normalized_corr) with delta = tA - tB so A_t = B_t + delta."""
    a_n = audio.normalize(env_a)
    b_n = audio.normalize(env_b)
    xc = signal.correlate(a_n, b_n, mode="full", method="fft")
    lags = np.arange(len(xc)) - (len(b_n) - 1)
    pk = int(np.argmax(xc))
    return float(lags[pk] / env_sr), float(xc[pk] / len(env_b))


def _refine(a, b, b_start_s, expected_delta, sr, probe_len_s=60.0, pad_s=1.5):
    pl = int(probe_len_s * sr)
    bs = int(b_start_s * sr)
    if bs + pl > len(b):
        return None
    probe = b[bs:bs + pl].astype(np.float32)
    a_center = b_start_s + expected_delta
    lo = max(0, int((a_center - pad_s) * sr))
    hi = min(len(a), int((a_center + pad_s + probe_len_s) * sr))
    if hi - lo < pl:
        return None
    seg = a[lo:hi].astype(np.float32)
    xc = signal.correlate(audio.normalize(seg), audio.normalize(probe),
                          mode="valid", method="fft")
    pk = int(np.argmax(np.abs(xc)))
    val = xc[pk] / len(probe)
    if 0 < pk < len(xc) - 1:
        y0, y1, y2 = xc[pk - 1], xc[pk], xc[pk + 1]
        denom = (y0 - 2 * y1 + y2)
        sub = 0.5 * (y0 - y2) / denom if abs(denom) > 1e-9 else 0.0
    else:
        sub = 0.0
    a_pos = (lo + pk + sub) / sr
    return float(a_pos - b_start_s), float(val)


def _multi_probe(a, b, expected_delta, b_dur, a_dur, sr, step_s=180.0):
    rs = []
    for bs in np.arange(60.0, b_dur - 60.0, step_s):
        a_center = bs + expected_delta
        if a_center < 1.5 or a_center + 60.0 + 1.5 > a_dur:
            continue
        r = _refine(a, b, bs, expected_delta, sr)
        if r:
            rs.append((bs, r[0], r[1]))
    return rs


def compute_sync(a, b, a_dur, b_dur, sr=SR, partial=False, verbose=False):
    """Align PCM array `b` (source) to `a` (reference). Returns SyncResult.

    `a`, `b` are mono float arrays at `sr` Hz. `a_dur`/`b_dur` are their
    durations in seconds (usually len/sr, passed explicitly so callers can use
    container duration). Raises SyncError in full-overlap mode when evidence is
    too weak.
    """
    env_a, esr = audio.log_envelope(a, sr)
    env_b, _ = audio.log_envelope(b, sr)
    coarse_d, coarse_v = coarse_offset(env_a, env_b, esr)
    if verbose:
        print("  coarse delta = %+.4fs (xc/N=%.3f)" % (coarse_d, coarse_v))

    probes = _multi_probe(a, b, coarse_d, b_dur, a_dur, sr)
    good = np.array([abs(p[2]) > GOOD_NCOEF for p in probes], dtype=bool)
    if verbose:
        print("  good probes: %d / %d" % (int(good.sum()), len(probes)))

    if good.sum() >= 3:
        bs_arr = np.array([p[0] for p in probes])
        d_arr = np.array([p[1] for p in probes])
        slope, intercept = np.polyfit(bs_arr[good], d_arr[good], 1)
        delta = float(slope * (b_dur / 2) + intercept)
        return SyncResult(delta, float(slope), coarse_v,
                          len(probes), int(good.sum()), None)
    if not partial:
        raise SyncError(
            "too few good probes (%d < 3); sync unreliable. If this is a "
            "short partial-coverage clip, use partial=True / --partial."
            % int(good.sum()))
    if probes:
        delta = float(np.median([p[1] for p in probes]))
        return SyncResult(delta, 0.0, coarse_v, len(probes),
                          int(good.sum()), "median")
    return SyncResult(float(coarse_d), 0.0, coarse_v, 0, 0, "coarse")


def _overlap(delta, a_dur, b_dur):
    ref_start = max(0.0, delta)
    ref_end = min(a_dur, delta + b_dur)
    return (ref_start, ref_end), (ref_start - delta, ref_end - delta)


def sync_files(reference, source, partial=False, verbose=True):
    """Extract audio from both files, compute the offset, write sidecar(s).

    In full-overlap mode writes a sidecar for BOTH inputs (reference gets
    delta=0). In partial mode writes only the source sidecar. Returns the
    source's sidecar Path.
    """
    reference, source = Path(reference), Path(source)
    a_dur = audio.media_duration(reference)
    b_dur = audio.media_duration(source)
    if verbose:
        print("Mode: %s" % ("partial-coverage" if partial else "full-overlap"))
        print("A (reference): %s  duration=%.3fs" % (reference.name, a_dur))
        print("B (source):    %s  duration=%.3fs" % (source.name, b_dur))

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        a_pcm, b_pcm = td / "a.pcm", td / "b.pcm"
        if verbose:
            print("Extracting mono PCM @ %d Hz..." % SR)
        audio.extract_pcm(reference, a_pcm, SR)
        audio.extract_pcm(source, b_pcm, SR)
        a = audio.read_pcm(a_pcm)
        b = audio.read_pcm(b_pcm)
        res = compute_sync(a, b, a_dur, b_dur, sr=SR, partial=partial,
                           verbose=verbose)

    if res.coarse_corr < 0.3 and verbose:
        print("  WARNING: low coarse correlation; sync may be unreliable.",
              file=sys.stderr)
    if verbose:
        msg = "  delta=%+.6fs  drift=%+.3e" % (res.delta_seconds, res.drift_slope)
        if res.fallback:
            msg += "  (fallback: %s)" % res.fallback
        print(msg)

    (ref_ovl, src_ovl) = _overlap(res.delta_seconds, a_dur, b_dur)
    if ref_ovl[1] - ref_ovl[0] < 1.0:
        raise SyncError("overlap window <1s; the two recordings barely share "
                        "content (delta=%.3fs)" % res.delta_seconds)

    src_sc = write_sidecar(
        source, source=source.name, reference=reference.name,
        delta_seconds=res.delta_seconds, drift_slope=res.drift_slope,
        overlap_in_reference=ref_ovl, overlap_in_source=src_ovl,
    )
    if verbose:
        print("Wrote %s" % src_sc)
    if not partial:
        ref_sc = write_sidecar(
            reference, source=reference.name, reference=reference.name,
            delta_seconds=0.0, drift_slope=0.0,
            overlap_in_reference=ref_ovl, overlap_in_source=ref_ovl,
        )
        if verbose:
            print("Wrote %s" % ref_sc)
    return src_sc
