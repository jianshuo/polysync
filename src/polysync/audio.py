"""Shared audio primitives — the pieces sync, verify, and edit all need.

Everything here is either pure numpy/scipy (unit-testable without media) or a
thin ffmpeg/ffprobe wrapper. Keeping these in one place is the whole reason
polysync is a package and not three copy-pasted scripts.
"""
import subprocess
from pathlib import Path

import numpy as np
from scipy import signal


def loudest_audio_stream(video_path):
    """Return the index N of the audio stream (`0:a:N`) with the highest mean
    volume, probed over a 60 s window mid-file.

    Why this matters: pro cameras often record multiple audio tracks where the
    first one is dead. Sony FX6 MXF clips carry 4 mono PCM tracks and commonly
    leave a:0 / a:1 silent (~-90 dB) with the real room mic on a:2 / a:3.
    Hard-coding `0:a:0` would cross-correlate silence and fail to sync, so pick
    the loudest track instead. Single-stream files (most MP4 cams) short-circuit
    to a:0.
    """
    video_path = Path(video_path)
    streams = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", str(video_path)],
        check=True, capture_output=True, text=True,
    ).stdout.strip().splitlines()
    if len(streams) <= 1:
        return 0
    best_idx, best_db = 0, -1e9
    for ch in range(len(streams)):
        err = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-ss", "300", "-t", "60",
             "-i", str(video_path), "-map", "0:a:%d" % ch,
             "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True,
        ).stderr
        for line in err.splitlines():
            if "mean_volume" in line:
                try:
                    db = float(line.split("mean_volume:")[1].strip().split()[0])
                except (IndexError, ValueError):
                    db = -1e9
                if db > best_db:
                    best_db, best_idx = db, ch
                break
    print("  [%s] loudest audio stream: a:%d (%.1f dB)"
          % (video_path.name, best_idx, best_db))
    return best_idx


def extract_pcm(video_path, dst, sr, stream=None):
    """Extract one audio track as mono signed-16 PCM at `sr` Hz.

    `stream` is the `0:a:N` index; if None, auto-select the loudest track.
    No `-itsoffset` is ever applied here — offsets are pure metadata and are
    handled by index arithmetic / `-itsoffset` at consume time downstream.
    """
    video_path = Path(video_path)
    ch = loudest_audio_stream(video_path) if stream is None else stream
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-i", str(video_path),
         "-map", "0:a:%d" % ch, "-ac", "1", "-ar", str(sr),
         "-f", "s16le", str(dst)],
        check=True, stderr=subprocess.DEVNULL,
    )


def read_pcm(path):
    """Read a raw s16le file into a float32 array."""
    return np.fromfile(str(path), dtype=np.int16).astype(np.float32)


def media_duration(path):
    """Container duration in seconds, via ffprobe."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        check=True, capture_output=True, text=True,
    )
    return float(out.stdout.strip())


def frame_rms(x, sr, hop_ms=10, win_ms=50):
    """Sliding-window RMS of `x`. Returns (rms_per_frame, frame_sr_hz).

    Uses a cumulative-sum trick so it's O(n) regardless of window size. This is
    the shared primitive behind both the sync envelope (log of this, high-passed)
    and the edit per-second loudness.
    """
    hop = int(sr * hop_ms / 1000)
    win = int(sr * win_ms / 1000)
    n = (len(x) - win) // hop + 1
    if n <= 0:
        return np.zeros(0, dtype=np.float32), sr / hop
    sq = x.astype(np.float64) ** 2
    csq = np.concatenate([[0.0], np.cumsum(sq)])
    out = np.empty(n, dtype=np.float32)
    for i in range(n):
        s = i * hop
        out[i] = np.sqrt(max(1e-9, (csq[s + win] - csq[s]) / win))
    return out, sr / hop


def log_envelope(x, sr, hop_ms=10, win_ms=50, highpass_hz=0.05):
    """Log-energy envelope, high-passed to strip slow gain/drift offsets.

    This is what sync cross-correlates: it captures dialogue/music dynamics
    that BOTH mics hear regardless of their frequency response — the reason
    the matcher is robust even when the two cameras have very different mics.
    """
    rms, fsr = frame_rms(x, sr, hop_ms, win_ms)
    env = np.log(rms + 1e-3)
    if highpass_hz:
        env = highpass(env, fsr, highpass_hz)
    return env, fsr


def highpass(x, fs, cut_hz=0.05):
    sos = signal.butter(2, cut_hz, btype="high", fs=fs, output="sos")
    return signal.sosfiltfilt(sos, x).astype(np.float32)


def normalize(x):
    x = x - x.mean()
    s = x.std()
    return x / s if s > 0 else x
