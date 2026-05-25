"""`polysync` command-line entry point.

    polysync sync        REFERENCE SOURCE [--partial]
    polysync verify      REFERENCE SOURCE SIDECAR [--apply-drift]
    polysync edit        IN1 IN2 ... --out edl.json [--mode rotation|greedy]
    polysync render-cuts EDL --out out.mp4
    polysync render-pip  EDL --out out.mp4 [--pip bottom-right]
"""
import argparse
import sys

from . import __version__
from .sync import sync_files, SyncError
from .verify import verify_files
from .edit import autoedit, render_cuts, render_pip

USAGE = __doc__


def _cmd_sync(argv):
    ap = argparse.ArgumentParser(prog="polysync sync")
    ap.add_argument("reference", help="Reference recording (defines the timeline)")
    ap.add_argument("source", help="Source to align to the reference")
    ap.add_argument("--partial", action="store_true",
                    help="Lenient mode for a source covering only part of the "
                         "reference's span; degrades gracefully, writes only the "
                         "source sidecar.")
    args = ap.parse_args(argv)
    try:
        sync_files(args.reference, args.source, partial=args.partial)
    except SyncError as e:
        print("ERROR: %s" % e, file=sys.stderr)
        return 1
    return 0


def _cmd_verify(argv):
    ap = argparse.ArgumentParser(prog="polysync verify")
    ap.add_argument("reference")
    ap.add_argument("source")
    ap.add_argument("sidecar", help="The source's <source>.sync.json")
    ap.add_argument("--apply-drift", action="store_true")
    ap.add_argument("--step", type=float, default=600.0,
                    help="Probe spacing in seconds (default 10 min)")
    args = ap.parse_args(argv)
    try:
        passed, _ = verify_files(args.reference, args.source, args.sidecar,
                                 step=args.step, apply_drift=args.apply_drift)
    except ValueError as e:
        print("ERROR: %s" % e, file=sys.stderr)
        return 2
    return 0 if passed else 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if argv[0] in ("-V", "--version"):
        print("polysync %s" % __version__)
        return 0

    cmd, rest = argv[0], argv[1:]
    dispatch = {
        "sync": _cmd_sync,
        "verify": _cmd_verify,
        "edit": lambda a: autoedit.main(a) or 0,
        "render-cuts": lambda a: render_cuts.main(a) or 0,
        "render-pip": lambda a: render_pip.main(a) or 0,
    }
    if cmd not in dispatch:
        print("Unknown command %r.\n%s" % (cmd, USAGE), file=sys.stderr)
        return 2
    return dispatch[cmd](rest)


if __name__ == "__main__":
    sys.exit(main())
