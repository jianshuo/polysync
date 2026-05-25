# polysync

**Multicam audio sync + director-style auto-edit.** Align N recordings of the
same event by audio cross-correlation, then cut or picture-in-picture them into
a single MP4 — driven entirely by who's talking.

What makes it different from "yet another sync tool":

- **Reversible sidecars, never re-encodes the originals.** Sync writes a tiny
  `<input>.sync.json` next to each file holding a single offset. A 75-min 4K
  3-camera shoot is 250+ GB; baking offsets into re-encoded copies would double
  that and lose quality. Downstream applies the offset with `ffmpeg -itsoffset`
  at consume time. Originals are touched read-only, always.
- **Envelope cross-correlation, not raw waveform.** Matches the log-energy
  envelope, which both mics hear regardless of their frequency response — robust
  even when a second camera's on-board mic sounds nothing like the main one.
- **Clock-drift aware.** Cheap recorders drift 5–50 ppm; polysync fits the drift
  across the recording and reports it separately, so long-form lip-sync can
  correct it while camera-cut editing can ignore it.
- **Handles the messy real cases.** Auto-picks the loudest audio track (pro
  cameras often leave track 1 dead), partial-coverage clips that only span part
  of the session, and independent verification of the result.

## Install

```bash
pip install polysync          # once published
# or, from a checkout:
pip install -e ".[dev]"
```

Requires **Python ≥ 3.9** and **ffmpeg / ffprobe** on your `PATH`
(`brew install ffmpeg`, `apt install ffmpeg`, …). Python deps: `numpy`, `scipy`.

## Quickstart

```bash
# 1. Sync each angle to a reference camera (writes <file>.sync.json sidecars)
polysync sync  CAM_A.mp4 CAM_B.mxf
polysync sync  CAM_A.mp4 CAM_C.mxf

# 2. (optional) Verify the alignment — re-checks residual independently
polysync verify CAM_A.mp4 CAM_B.mxf CAM_B.mxf.sync.json

# 3. Build an auto-edit decision list (who's on screen each second)
polysync edit  CAM_A.mp4 CAM_B.mxf CAM_C.mxf --out edl.json

# 4. Render — hard cuts, or with a picture-in-picture inset
polysync render-cuts edl.json --out final.mp4
polysync render-pip  edl.json --out final.mp4 --pip bottom-right
```

A clip that only covers **part** of the session (a Riverside / phone / lavalier
recording that started mid-way):

```bash
polysync sync REFERENCE.mp4 PARTIAL.m4a --partial
```

## How it consumes the sidecar

`delta_seconds` is the source's `t=0` in the reference's timeline (positive =
source starts later). To align by hand:

```bash
ffmpeg -itsoffset $(jq -r .delta_seconds CAM_B.mxf.sync.json) -i CAM_B.mxf \
       -i CAM_A.mp4 -filter_complex "[0:v][1:v]hstack" out.mp4
```

The `edit` / `render-*` commands read every sidecar automatically.

## Python API

```python
from polysync import compute_sync           # pure-numpy core, unit-testable
from polysync.sync import sync_files         # file → sidecar
from polysync.verify import verify_files
from polysync.edit import build_edl
```

## Status

Beta (0.1). Sync + verify are battle-tested on real Sony FX3/FX6 multicam
interview footage; the auto-edit is audio-energy-driven (no face detection).
Issues and PRs welcome.

## License

MIT © 王建硕 (Jian Shuo Wang)
