#!/usr/bin/env python3
"""Generate two synthetic WAV files of the same "event" with a known offset,
so you can try the whole polysync flow without any real footage.

    python examples/make_demo_audio.py
    polysync sync   demo_ref.wav demo_cam.wav
    polysync verify demo_ref.wav demo_cam.wav demo_cam.wav.sync.json

`demo_cam.wav` starts DEMO_OFFSET_S seconds after `demo_ref.wav` and is recorded
at a different gain with added noise — i.e. a second mic hearing the same room.
`polysync sync` should recover an offset very close to DEMO_OFFSET_S.
"""
import wave
from pathlib import Path

import numpy as np

SR = 16000
DURATION_S = 900          # long enough for several drift probes
DEMO_OFFSET_S = 41.36     # demo_cam starts this many seconds after demo_ref


def event_signal(duration_s, seed):
    """Speech-ish: random band-limited noise bursts over a quiet bed."""
    rng = np.random.default_rng(seed)
    n = int(duration_s * SR)
    x = rng.standard_normal(n).astype(np.float32) * 30.0
    t = 0
    while t < n:
        t += int(rng.uniform(0.3, 1.5) * SR)
        burst = int(rng.uniform(0.4, 1.2) * SR)
        if t + burst > n:
            break
        x[t:t + burst] += (rng.standard_normal(burst).astype(np.float32)
                           * 4000.0 * np.hanning(burst).astype(np.float32))
        t += burst
    return x


def write_wav(path, x):
    x = np.clip(x, -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(x.tobytes())


def main():
    out = Path(__file__).parent
    ref = event_signal(DURATION_S, seed=7)
    off = int(DEMO_OFFSET_S * SR)
    rng = np.random.default_rng(3)
    cam = ref[off:] * 0.5 + rng.standard_normal(len(ref) - off).astype(np.float32) * 200.0

    write_wav(out / "demo_ref.wav", ref)
    write_wav(out / "demo_cam.wav", cam)
    print("Wrote %s and %s (demo_cam starts %.2fs after demo_ref)."
          % (out / "demo_ref.wav", out / "demo_cam.wav", DEMO_OFFSET_S))
    print("\nNext:")
    print("  polysync sync   examples/demo_ref.wav examples/demo_cam.wav")
    print("  polysync verify examples/demo_ref.wav examples/demo_cam.wav "
          "examples/demo_cam.wav.sync.json")
    print("\nExpected delta_seconds ~= %.2f" % DEMO_OFFSET_S)


if __name__ == "__main__":
    main()
