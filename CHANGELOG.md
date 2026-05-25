# Changelog

All notable changes to polysync are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versioning is [SemVer](https://semver.org/).

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
