# Changelog

All notable changes to polysync are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versioning is [SemVer](https://semver.org/).

## [0.3.0] — 2026-05-26

### Added
- **Speaker-gated soundtrack** (`polysync.edit.audiomix`): `--duck-audio` on
  `render-cuts` / `render-pip`. Per moment it keeps the ACTIVE speaker's mic at
  full level and ducks the rest (active speaker decided by each mic's energy
  relative to its own baseline). Far cleaner than a single-cam mic for
  interviews — kills the constant bleed/room buildup that loudness-norm pumps up.
  `--duck-db` sets the ducked level (default -18 dB); finished with high-pass +
  light FFT denoise + loudness-normalize.
- `--audio-cams 0,1` — explicitly choose which mics to gate among, to exclude a
  wide/room mic that sits at a similar level to a real speaker mic (level alone
  can't separate a close lav from a near room mic).

## [0.2.0] — 2026-05-26

Render-side handling for raw camera footage — the hard-won lessons from a real
Sony FX3/FX6 vertical shoot.

### Added
- `polysync.edit.grade` module: `make_slog3_709_lut` generates a Sony S-Log3 /
  S-Gamut3.Cine → Rec.709 3D LUT; `segment_filter` builds per-shot filter chains.
- `render-cuts` / `render-pip` new flags:
  - `--log slog3` — convert flat S-Log3 footage to Rec.709 (auto-generates+caches the LUT).
  - `--lut FILE.cube` — apply any 3D LUT.
  - `--rotate CAM:DEG` (repeatable) — per-camera rotation for vertically-shot
    cameras that wrote no rotation flag (e.g. FX6 turned on its side; 90 = CW).
  - `--fill` (render-cuts) — crop-to-fill instead of letterbox-pad, for vertical
    delivery (1080×1920) with no black bars.

### Changed
- LUT is applied **after** the downscale, not before — a 3D LUT on 4K is ~4× slower
  than on 1080p for an identical result.

## [0.1.0] — 2026-05-25

Initial release. Extracted and refactored from the `wjs-syncing-multicam` and
`wjs-editing-multicam` skills into one installable package with a shared core.

### Added
- `polysync sync` — envelope cross-correlation alignment with multi-probe clock
  drift fit; writes reversible `<input>.sync.json` sidecars (originals never
  re-encoded). `--partial` mode for sources covering only part of the session.
- `polysync verify` — independent residual check via numpy index arithmetic
  (not `ffmpeg -itsoffset`, which is a no-op on headerless raw PCM).
- `polysync edit` — audio-energy-driven multicam EDL builder (rotation / greedy).
- `polysync render-cuts` / `polysync render-pip` — render the EDL to one MP4.
- Automatic loudest-audio-track selection (handles pro cameras whose first
  track is dead, e.g. Sony FX6 MXF).
- Pure-numpy `compute_sync` with synthetic-audio unit tests (no ffmpeg needed).

### Fixed (relative to the source skills)
- Verification falsely failing because `-itsoffset` doesn't pad headerless raw
  PCM — now applies the offset by index arithmetic.
- Auto-edit crashing when no camera covers `t=0` (overlap windows that start a
  few seconds in) — now opens at the first covered second.
