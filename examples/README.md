# polysync examples

## Zero-footage demo

You don't need real video to see the whole flow. Generate two synthetic WAVs of
the same "event" with a known offset:

```bash
python examples/make_demo_audio.py
```

This writes `demo_ref.wav` and `demo_cam.wav`, where `demo_cam` starts ~41.36 s
after `demo_ref` (different gain + added noise, mimicking a second mic). Then:

```bash
polysync sync   examples/demo_ref.wav examples/demo_cam.wav
polysync verify examples/demo_ref.wav examples/demo_cam.wav examples/demo_cam.wav.sync.json
```

`polysync sync` should report `delta ≈ +41.36s`, and `verify` should PASS with a
sub-frame median residual. Look at `examples/demo_cam.wav.sync.json` to see the
sidecar polysync writes next to every input.

> The WAV files and their sidecars are git-ignored — they're regenerated on demand.

## Real multicam

With actual angles (any format ffmpeg reads):

```bash
polysync sync  CAM_A.mp4 CAM_B.mxf          # reference first
polysync sync  CAM_A.mp4 CAM_C.mxf
polysync edit  CAM_A.mp4 CAM_B.mxf CAM_C.mxf --out edl.json
polysync render-pip edl.json --out final.mp4 --pip bottom-right
```
