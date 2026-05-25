"""Render an autoedit EDL with picture-in-picture for 1, 2, or N cameras.

- 1 cam: pass-through (no inset).
- 2 cam: main = active cam; PiP = the other cam at the same time range.
- N cam: main = active; PiP = a covered non-active cam (round-robin or first).

Per-segment EDL rows may carry a `pip` field (cam index) to override the picker.
"""
import argparse
import json
import subprocess
from pathlib import Path

POSITIONS = {
    "bottom-right": ("W-w-{m}", "H-h-{m}"),
    "top-right":    ("W-w-{m}", "{m}"),
    "bottom-left":  ("{m}",     "H-h-{m}"),
    "top-left":     ("{m}",     "{m}"),
}


def pick_pip(row, K, coverage, mode="next"):
    """Choose the PiP cam for a segment, among cams covered for the WHOLE
    segment. Honours an explicit row['pip']. Returns None if no other cam fits."""
    if row.get("pip") is not None:
        return int(row["pip"])
    cam = row["cam"]
    s, e = row["start"], row["end"]
    candidates = [k for k in range(K)
                  if k != cam and coverage[k][0] <= s and coverage[k][1] >= e]
    if not candidates:
        return None
    if mode == "next":
        for off in range(1, K):
            cand = (cam + off) % K
            if cand in candidates:
                return cand
    return candidates[0]


def render_pip(edl_path, out, encoder="hevc_videotoolbox", bitrate="12M",
               width=1920, height=1080, fps=30, pip="bottom-right",
               pip_width=480, pip_margin=24, border_px=4, pip_pick="next",
               run=True):
    plan = json.loads(Path(edl_path).read_text())
    inputs = plan["inputs"]
    deltas = plan.get("deltas", [0.0] * len(inputs))
    edl = plan["edl"]
    audio_src = plan["audio_source"]
    K = len(inputs)
    coverage = plan.get("coverage", [[0.0, plan["duration_sec"]]] * K)

    W, H = width, height
    pw = pip_width
    ph = round(pw * 9 / 16)
    bw = border_px
    x_expr, y_expr = POSITIONS[pip]
    x_expr = x_expr.format(m=pip_margin)
    y_expr = y_expr.format(m=pip_margin)

    cmd = ["ffmpeg", "-nostdin", "-y"]
    for src, dlt in zip(inputs, deltas):
        if abs(dlt) > 1e-9:
            cmd.extend(["-itsoffset", "%.6f" % dlt])
        cmd.extend(["-i", src])

    filters = []
    for i, row in enumerate(edl):
        cam = row["cam"]
        s, e = row["start"], row["end"]
        main_label = "m%d" % i if K > 1 else "v%d" % i
        filters.append(
            "[%d:v]trim=start=%s:end=%s,setpts=PTS-STARTPTS,"
            "scale=%d:%d:force_original_aspect_ratio=decrease,"
            "pad=%d:%d:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=%d[%s]"
            % (cam, s, e, W, H, W, H, fps, main_label)
        )
        if K == 1:
            continue
        pip_cam = pick_pip(row, K, coverage, mode=pip_pick)
        if pip_cam is None:
            filters.append("[m%d]copy[v%d]" % (i, i))
            continue
        chain = (
            "[%d:v]trim=start=%s:end=%s,setpts=PTS-STARTPTS,"
            "scale=%d:%d:force_original_aspect_ratio=decrease,"
            "pad=%d:%d:(ow-iw)/2:(oh-ih)/2,"
            % (pip_cam, s, e, pw, ph, pw, ph)
        )
        if bw > 0:
            chain += "pad=%d:%d:%d:%d:white," % (pw + 2 * bw, ph + 2 * bw, bw, bw)
        chain += "setsar=1,fps=%d[p%d]" % (fps, i)
        filters.append(chain)
        filters.append("[m%d][p%d]overlay=%s:%s:eof_action=pass[v%d]"
                       % (i, i, x_expr, y_expr, i))

    concat = "".join("[v%d]" % i for i in range(len(edl)))
    filters.append("%sconcat=n=%d:v=1:a=0[vout]" % (concat, len(edl)))
    audio_offset = edl[0]["start"] if edl else 0.0
    dur = plan["duration_sec"]
    fc = ";".join(filters)
    fc += (";[%d:a:0]atrim=start=%s:duration=%s,asetpts=PTS-STARTPTS[aout]"
           % (audio_src, audio_offset, dur))
    cmd.extend([
        "-filter_complex", fc,
        "-map", "[vout]", "-map", "[aout]",
        "-t", str(dur),
        "-c:v", encoder, "-b:v", bitrate, "-tag:v", "hvc1",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", str(out),
    ])
    if run:
        print("PiP %dx%d, inset %dx%d (+%dpx) at %s; %d cams; %d segments"
              % (W, H, pw, ph, bw, pip, K, len(edl)))
        subprocess.run(cmd, check=True)
    return cmd


def main(argv=None):
    ap = argparse.ArgumentParser(prog="polysync render-pip")
    ap.add_argument("edl", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--encoder", default="hevc_videotoolbox")
    ap.add_argument("--bitrate", default="12M")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--pip", choices=list(POSITIONS), default="bottom-right")
    ap.add_argument("--pip-width", type=int, default=480)
    ap.add_argument("--pip-margin", type=int, default=24)
    ap.add_argument("--border-px", type=int, default=4)
    ap.add_argument("--pip-pick", choices=["next", "second-best"], default="next")
    args = ap.parse_args(argv)
    render_pip(args.edl, args.out, encoder=args.encoder, bitrate=args.bitrate,
               width=args.width, height=args.height, fps=args.fps, pip=args.pip,
               pip_width=args.pip_width, pip_margin=args.pip_margin,
               border_px=args.border_px, pip_pick=args.pip_pick)


if __name__ == "__main__":
    main()
