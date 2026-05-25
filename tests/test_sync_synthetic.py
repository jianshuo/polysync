"""Synthetic-audio tests for the sync core.

No ffmpeg, no media files: we generate a noise-burst "event" signal, make a
second copy shifted by a known offset (and optionally with a different gain /
added noise to mimic a second mic), and assert compute_sync recovers the
offset within a few milliseconds.
"""
import numpy as np
import pytest

from polysync.sync import compute_sync, coarse_offset, SyncError
from polysync import audio

SR = 8000


def _event_signal(duration_s, seed=0):
    """Speech-ish: random bursts of band-limited noise on a quiet bed."""
    rng = np.random.default_rng(seed)
    n = int(duration_s * SR)
    x = rng.standard_normal(n).astype(np.float32) * 30.0  # quiet floor
    t = 0
    while t < n:
        gap = int(rng.uniform(0.3, 1.5) * SR)
        burst = int(rng.uniform(0.4, 1.2) * SR)
        t += gap
        if t + burst > n:
            break
        env = np.hanning(burst).astype(np.float32)
        x[t:t + burst] += rng.standard_normal(burst).astype(np.float32) * 4000.0 * env
        t += burst
    return x


def _shift(x, delta_samples, gain=1.0, noise=0.0, seed=1):
    """Return y where y's t=0 sits `delta_samples` after x's t=0.

    Positive delta => y starts later, i.e. y is x with `delta` leading samples
    dropped (y[0] corresponds to x[delta])."""
    rng = np.random.default_rng(seed)
    if delta_samples >= 0:
        y = x[delta_samples:].copy()
    else:
        y = np.concatenate([np.zeros(-delta_samples, dtype=np.float32), x])
    y = y * gain
    if noise:
        y = y + rng.standard_normal(len(y)).astype(np.float32) * noise
    return y


@pytest.mark.parametrize("delta_s", [12.34, 41.36, 120.5])
def test_recovers_known_offset(delta_s):
    # 900 s so the 180 s probe spacing yields >=3 probes inside the overlap.
    a = _event_signal(900, seed=7)
    delta_samples = int(delta_s * SR)
    # b is the reference content starting `delta_s` later than a's t=0, with a
    # different gain and added noise (second mic). So a_t = b_t + delta_s.
    b = _shift(a, delta_samples, gain=0.5, noise=200.0, seed=3)
    a_dur, b_dur = len(a) / SR, len(b) / SR
    res = compute_sync(a, b, a_dur, b_dur, sr=SR)
    assert abs(res.delta_seconds - delta_s) < 0.02, res
    assert res.n_good >= 3


def test_coarse_offset_sign_and_magnitude():
    a = _event_signal(200, seed=11)
    delta_s = 8.0
    b = _shift(a, int(delta_s * SR), gain=1.0, noise=50.0)
    env_a, esr = audio.log_envelope(a, SR)
    env_b, _ = audio.log_envelope(b, SR)
    coarse_d, corr = coarse_offset(env_a, env_b, esr)
    assert abs(coarse_d - delta_s) < 0.1
    assert corr > 0.3


def test_full_overlap_raises_on_garbage():
    # Two unrelated signals: no real offset, should fail loudly in strict mode.
    a = _event_signal(300, seed=1)
    b = _event_signal(300, seed=999)  # independent → no consistent peak
    with pytest.raises(SyncError):
        compute_sync(a, b, len(a) / SR, len(b) / SR, sr=SR, partial=False)


def test_partial_mode_degrades_instead_of_raising():
    # Same garbage, but partial mode must not raise — it falls back.
    a = _event_signal(300, seed=1)
    b = _event_signal(120, seed=999)
    res = compute_sync(a, b, len(a) / SR, len(b) / SR, sr=SR, partial=True)
    assert res.fallback in ("median", "coarse")
