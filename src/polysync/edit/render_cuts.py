"""Render an autoedit EDL into one MP4 with hard cuts (no transitions / PiP).

Applies each input's `delta` via `ffmpeg -itsoffset` so EDL times (reference
timeline) work directly inside the filter graph — originals are read untouched.
"""
import argparse
import json
import subprocess
from pathlib import Path


def render_cuts(edl_path, out, encoder="hevc_videotoolbox", bitrate="12M",
                width=1920, height=1080, fps=30, run=True):
    plan = json.loads(Path(edl_path).read_text())
    inputs = plan["inputs"]
    deltas = plan.get("deltas", [0.0] * len(inputs))
    edl = plan["edl"]
    audio_src = plan["audio_source"]
    W, H = width, height

    cmd = ["ffmpeg", "-nostdin", "-y"]
    for src, dlt in zip(inputs, deltas):
        if abs(dlt) > 1e-9:
            cmd.extend(["-itsoffset", "%.6f" % dlt])
        cmd.extend(["-i", src])

    filters = []
    for i, row in enumerate(edl):
        filters.append(
            "[%d:v]trim=start=%s:end=%s,setpts=PTS-STARTPTS,"
            "scale=%d:%d:force_original_aspect_ratio=decrease,"
            "pad=%d:%d:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=%d[v%d]"
            % (row["cam"], row["start"], row["end"], W, H, W, H, fps, i)
        )
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
    args = ap.parse_args(argv)
    render_cuts(args.edl, args.out, encoder=args.encoder, bitrate=args.bitrate,
                width=args.width, height=args.height, fps=args.fps)


if __name__ == "__main__":
    main()
