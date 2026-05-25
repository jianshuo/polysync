"""Speaker-gated ("ducked") audio mix for multicam interviews.

The default render takes a single camera's mic as the soundtrack. With close,
bleeding mics that's noisy: every mic also picks up the *other* speaker plus room
tone, so a constant sum sounds muddy and loudness-normalization pumps the bleed
up during pauses. This builds a cleaner track instead: per moment, keep only the
ACTIVE speaker's mic at full level and duck the rest.

Who's active is decided by each mic's energy relative to ITS OWN baseline (not
absolute level) — that's what tracks the talker despite a louder close mic
bleeding. Far/room mics (e.g. a wide establishing cam) are auto-excluded: any
mic whose overall level is >`exclude_db` below the loudest is dropped as an
audio candidate so the reverby room mic is never selected.

`build_ducked_audio` returns a finished wav (gated → high-pass → light denoise →
loudness-normalized). The renderers use it in place of the single-cam audio when
`--duck-audio` is passed.
"""
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from .. import audio


def _aligned_mic(path, delta, sr, n):
    """Extract a cam's loudest mic at `sr`, shifted into the reference timeline
    (so index t corresponds to reference second t/sr), length `n` samples."""
    with tempfile.NamedTemporaryFile(suffix=".pcm", delete=False) as tf:
        tmp = tf.name
    audio.extract_pcm(path, tmp, sr)          # loudest stream, mono
    x = audio.read_pcm(tmp)
    Path(tmp).unlink(missing_ok=True)
    pad = int(round(delta * sr))
    if pad > 0:
        x = np.concatenate([np.zeros(pad, np.float32), x])
    elif pad < 0:
        x = x[-pad:]
    if len(x) < n:
        x = np.pad(x, (0, n - len(x)))
    return x[:n]


def build_ducked_audio(inputs, deltas, coverage, duration, out_path, sr=48000,
                       duck_db=-18.0, frame_ms=100.0, margin=0.20,
                       exclude_db=14.0, audio_cams=None, verbose=True):
    """Write a speaker-gated, cleaned wav to `out_path`. Returns out_path.

    `audio_cams` (list of cam indices) explicitly picks which mics to gate among
    — use it to exclude a wide/room mic that sits at a similar LEVEL to a real
    speaker mic (level alone can't tell a close lav from a near room mic). If
    None, fall back to dropping any mic >`exclude_db` below the loudest.
    """
    n = int(duration * sr)
    mics = [_aligned_mic(p, d, sr, n) for p, d in zip(inputs, deltas)]

    lvl = np.array([20 * np.log10(np.sqrt(np.mean(m ** 2)) + 1e-6) for m in mics])
    if audio_cams:
        keep = [k for k in audio_cams if 0 <= k < len(mics)]
    else:
        keep = [k for k in range(len(mics)) if lvl[k] >= lvl.max() - exclude_db]
    if verbose:
        print("  mic levels(dB): %s; audio candidates: %s"
              % ([round(float(x), 1) for x in lvl], keep))

    hop = int(frame_ms / 1000 * sr)
    nf = n // hop

    def frame_logE(x):
        return np.array([np.log(np.sqrt(np.mean(x[i*hop:(i+1)*hop] ** 2) + 1) + 1)
                         for i in range(nf)])

    # coverage mask per cam (frames where the cam has valid footage in ref time)
    cov = np.zeros((len(mics), nf), dtype=bool)
    for k in range(len(mics)):
        s, e = coverage[k] if k < len(coverage) else (0.0, duration)
        cov[k, max(0, int(s/ (frame_ms/1000))): int(e/(frame_ms/1000))] = True

    # baseline-normalized energy per candidate
    norm = np.full((len(mics), nf), -1e9)
    for k in keep:
        E = frame_logE(mics[k])
        base = np.median(E[cov[k]]) if cov[k].any() else np.median(E)
        norm[k] = np.where(cov[k], E - base, -1e9)

    # active cam per frame = argmax normalized among covered candidates
    active = np.full(nf, keep[0], dtype=int)
    for f in range(nf):
        vals = [(norm[k, f], k) for k in keep if cov[k, f]]
        if vals:
            active[f] = max(vals)[1]

    # gain mask per cam (active=1 else duck), smoothed to crossfade
    duck = 10 ** (duck_db / 20.0)
    out = np.zeros(n, dtype=np.float32)
    ker = np.ones(int(0.2 * sr)) / int(0.2 * sr)
    for k in range(len(mics)):
        if k not in keep:
            continue
        gf = np.where(active == k, 1.0, duck)
        gs = np.repeat(gf, hop)
        gs = np.pad(gs, (0, n - len(gs)), mode="edge")
        gs = np.convolve(gs, ker, "same")
        out += mics[k] * gs

    pk = np.max(np.abs(out))
    if pk > 0:
        out *= 0.95 * 32767 / pk
    with tempfile.NamedTemporaryFile(suffix=".pcm", delete=False) as tf:
        raw = tf.name
    out.astype(np.int16).tofile(raw)

    # high-pass rumble, light FFT denoise, loudness-normalize -> finished wav
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "s16le", "-ar", str(sr), "-ac", "1", "-i", raw,
         "-af", "highpass=f=70,afftdn=nr=10,loudnorm=I=-16:TP=-1.5:LRA=11",
         "-ar", str(sr), "-ac", "2", str(out_path)],
        check=True,
    )
    Path(raw).unlink(missing_ok=True)
    return out_path
