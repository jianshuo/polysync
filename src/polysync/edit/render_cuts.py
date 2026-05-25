"""Render an autoedit EDL into one MP4 with hard cuts (no transitions / PiP).

Applies each input's `delta` via `ffmpeg -itsoffset` so EDL times (reference
timeline) work directly inside the filter graph — originals are read untouched.

Raw footage usually needs `--log slog3` (Sony S-Log3 -> Rec.709 grade) and, for
vertically-shot cameras with no rotation flag, `--rotate cam:90`. For vertical
delivery (小红书 / Reels / Shorts) pass `--width 1080 --height 1920 --fill`.
"""
import argparse
import json
import subprocess
from pathlib import Path

from .grade import resolve_lut, parse_rotate, segment_filter


def render_cuts(edl_path, out, encoder="hevc_videotoolbox", bitrate="12M",
                width=1920, height=1080, fps=30, lut=None, log=None,
                rotate=None, fill=False, run=True):
    plan = json.loads(Path(edl_path).read_text())
    inputs = plan["inputs"]
    deltas = plan.get("deltas", [0.0] * len(inputs))
    edl = plan["edl"]
    audio_src = plan["audio_source"]
    W, H = width, height
    lut_path = resolve_lut(lut, log)
    rot = parse_rotate(rotate)

    cmd = ["ffmpeg", "-nostdin", "-y"]
    for src, dlt in zip(inputs, deltas):
        if abs(dlt) > 1e-9:
            cmd.extend(["-itsoffset", "%.6f" % dlt])
        cmd.extend(["-i", src])

    filters = [
        segment_filter(row["cam"], row["start"], row["end"], i, W, H, fps,
                       rotate_deg=rot.get(row["cam"], 0), lut=lut_path, pip=fill)
        for i, row in enumerate(edl)
    ]
    concat = "".join("[v%d]" % i for i in range(len(edl)))
    filters.append("%sconcat=n=%d:v=1:a=0[vout]" % (concat, len(edl)))
    fc = ";".join(filters)

    audio_offset = edl[0]["start"] if edl else 0.0
    duration = plan["duration_sec"]
    fc += (";[%d:a:0]atrim=start=%s:duration=%s,asetpts=PTS-STARTPTS[aout]"
           % (audio_src, audio_offset, duration))
    cmd.extend([
        "-filter_complex", fc,
        "-map", "[vout]", "-map", "[aout]",
        "-t", str(duration),
        "-c:v", encoder, "-b:v", bitrate, "-tag:v", "hvc1",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", str(out),
    ])
    if run:
        print(" ".join(cmd))
        subprocess.run(cmd, check=True)
    return cmd


def main(argv=None):
    ap = argparse.ArgumentParser(prog="polysync render-cuts")
    ap.add_argument("edl", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--encoder", default="hevc_videotoolbox")
    ap.add_argument("--bitrate", default="12M")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--lut", help="3D LUT (.cube) applied after downscale")
    ap.add_argument("--log", help="built-in log->Rec.709 grade (e.g. slog3)")
    ap.add_argument("--rotate", action="append",
                    help="per-cam rotation CAM:DEG (90=CW), repeatable")
    ap.add_argument("--fill", action="store_true",
                    help="crop to fill instead of letterbox-pad (use for vertical)")
    args = ap.parse_args(argv)
    render_cuts(args.edl, args.out, encoder=args.encoder, bitrate=args.bitrate,
                width=args.width, height=args.height, fps=args.fps,
                lut=args.lut, log=args.log, rotate=args.rotate, fill=args.fill)


if __name__ == "__main__":
    main()
