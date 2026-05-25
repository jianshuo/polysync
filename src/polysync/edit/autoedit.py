"""Build a director-style EDL from N synced camera angles.

Inputs are ORIGINAL untouched media; each should have a `<input>.sync.json`
sidecar (from `polysync sync`). Sidecars give per-cam `delta_seconds` and
`overlap_in_reference`. Missing sidecar => cam assumed at delta=0, full coverage.

Decisions are audio-energy-driven only: per second, the cam whose mic is
loudest relative to the others (active-speaker proxy) wins, subject to dwell
hysteresis and coverage. No face/framing detection.
"""
import argparse
import json
import tempfile
import warnings
from pathlib import Path

import numpy as np

from .. import audio
from ..sidecar import read_sidecar, SCHEMA_VERSION

SR = 16000
FRAME_HZ = 1.0
ENV_HOP_MS = 100
ENV_WIN_MS = 200


def _per_sec_envelope(x):
    """Log-RMS envelope of `x` collapsed to one value per reference second."""
    rms, fsr = audio.frame_rms(x, SR, hop_ms=ENV_HOP_MS, win_ms=ENV_WIN_MS)
    env = np.log(rms + 1e-3)
    return env, fsr


def _lift_to_reference(env, env_sr, delta_sec, total_ref_sec):
    """Lift a cam-local per-frame envelope into the reference timeline at 1 Hz.

    Reference second t reads the cam's local second (t - delta_sec). Seconds
    outside the cam's recorded range become -inf so the editor never picks them.
    """
    n_per = int(env_sr / FRAME_HZ)
    take = (len(env) // n_per) * n_per
    local = env[:take].reshape(-1, n_per).mean(axis=1) if take else np.zeros(0)
    out = np.full(total_ref_sec, -np.inf, dtype=np.float32)
    for t in range(total_ref_sec):
        tl = int(t - delta_sec)
        if 0 <= tl < len(local):
            out[t] = local[tl]
    return out


def _coverage_from_sidecar(input_path, total):
    _, ovl, _ = read_sidecar(input_path)
    if ovl is None:
        return (0.0, float(total))
    return (max(0.0, ovl[0]), min(float(total), ovl[1]))


def _parse_coverage_flag(values, k_total, total):
    cov = [(0.0, float(total))] * k_total
    for v in (values or []):
        parts = v.split(":")
        if len(parts) != 3:
            raise SystemExit("--coverage expects CAM:START:END, got %r" % v)
        k = int(parts[0])
        if not (0 <= k < k_total):
            raise SystemExit("--coverage cam %d out of range" % k)
        cov[k] = (float(parts[1]), float(parts[2]))
    return cov


def _covered_at(cov, t):
    return [k for k, (s, e) in enumerate(cov) if s <= t < e]


def rotation_edit(scores, coverage, min_dwell=8, max_dwell=15,
                  opening_dwell=10, seed=42):
    """Alternate among covered cams with varying dwell; force a switch when the
    active cam leaves coverage."""
    T, K = scores.shape
    rng = np.random.default_rng(seed)
    seq = np.full(T, -1, dtype=np.int32)

    def best_at(t, candidates, win=opening_dwell):
        end = min(T, t + win)
        return max(candidates,
                   key=lambda k: scores[t:end, k].mean() if end > t else scores[t, k])

    # The overlap window often starts a few seconds in (no cam covers t=0).
    # Open at the first covered second; leading seconds are backfilled below.
    cur_set = _covered_at(coverage, 0)
    t_open = 0
    if not cur_set:
        t_open = next((t for t in range(T) if _covered_at(coverage, t)), -1)
        if t_open < 0:
            raise SystemExit("No camera is covered at any time")
        cur_set = _covered_at(coverage, t_open)
    cur = best_at(t_open, cur_set)
    t = t_open
    while t < T:
        dwell = int(rng.integers(min_dwell, max_dwell + 1))
        end = t
        while end < t + dwell and end < T:
            if cur not in _covered_at(coverage, end):
                break
            seq[end] = cur
            end += 1
        if end >= T:
            break
        cands = [k for k in _covered_at(coverage, end) if k != cur]
        if not cands:
            cands = _covered_at(coverage, end)
            if not cands:
                seq[end] = cur
                t = end + 1
                continue
        upcoming = min(T, end + 6)
        cur = max(cands, key=lambda k: scores[end:upcoming, k].mean()
                  if upcoming > end else scores[end, k])
        t = end
    for t in range(T):
        if seq[t] == -1:
            cands = _covered_at(coverage, t)
            seq[t] = cands[0] if cands else 0
    return seq


def greedy_edit(scores, coverage, min_dwell=4, max_dwell=18, lookahead=4,
                switch_threshold=0.0, opening_dwell=8):
    """Greedy hard-cut editor with min/max dwell hysteresis."""
    T, K = scores.shape

    def win_mean(t, k, w):
        end = min(T, t + w)
        return scores[t:end, k].mean() if end > t else scores[t, k]

    seq = np.full(T, -1, dtype=np.int32)
    cands0 = _covered_at(coverage, 0)
    t_open = 0
    if not cands0:
        t_open = next((t for t in range(T) if _covered_at(coverage, t)), -1)
        if t_open < 0:
            raise SystemExit("No camera is covered at any time")
        cands0 = _covered_at(coverage, t_open)
    seq[t_open] = max(cands0, key=lambda k: win_mean(t_open, k, opening_dwell))
    streak = 1
    for t in range(t_open + 1, T):
        cur = seq[t - 1]
        if cur not in _covered_at(coverage, t):
            cands = [k for k in _covered_at(coverage, t) if k != cur] or _covered_at(coverage, t)
            if not cands:
                seq[t] = cur; streak += 1; continue
            seq[t] = max(cands, key=lambda k: win_mean(t, k, lookahead))
            streak = 1; continue
        if streak < min_dwell:
            seq[t] = cur; streak += 1; continue
        cands = [k for k in _covered_at(coverage, t) if k != cur]
        if not cands:
            seq[t] = cur; streak += 1; continue
        if streak >= max_dwell:
            seq[t] = max(cands, key=lambda k: win_mean(t, k, lookahead))
            streak = 1; continue
        cur_s = win_mean(t, cur, lookahead)
        best_k = max(cands, key=lambda k: win_mean(t, k, lookahead))
        if win_mean(t, best_k, lookahead) > cur_s + switch_threshold:
            seq[t] = best_k; streak = 1
        else:
            seq[t] = cur; streak += 1
    # Backfill any leading uncovered seconds (before t_open) with a covered cam.
    for t in range(T):
        if seq[t] == -1:
            cands = _covered_at(coverage, t)
            seq[t] = cands[0] if cands else 0
    return seq


def edl_from_seq(seq):
    edl = []
    i = 0
    while i < len(seq):
        j = i
        while j < len(seq) and seq[j] == seq[i]:
            j += 1
        edl.append({"start": float(i), "end": float(j), "cam": int(seq[i])})
        i = j
    return edl


def build_edl(inputs, mode="rotation", audio_source=None, min_dwell=8,
              max_dwell=15, switch_threshold=0.0, seed=42, coverage_flags=None,
              verbose=True):
    """Compute the EDL plan dict for a list of input paths."""
    inputs = [Path(p) for p in inputs]
    K = len(inputs)

    deltas, cov_from_sc, has_sc = [], [], []
    for p in inputs:
        d, ovl, has = read_sidecar(p)
        deltas.append(d); cov_from_sc.append(ovl); has_sc.append(has)
    missing = [p.name for p, h in zip(inputs, has_sc) if not h]
    if missing and verbose:
        print("WARN: no sidecar for %s; assuming delta=0, full coverage. "
              "Run `polysync sync` first if these should be offset." % missing)

    durations, envs = [], []
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        for i, p in enumerate(inputs):
            out = td / ("%d.pcm" % i)
            audio.extract_pcm(p, out, SR)
            x = audio.read_pcm(out)
            durations.append(len(x) / SR)
            envs.append(_per_sec_envelope(x))

    cov_ends = [ovl[1] for ovl in cov_from_sc if ovl is not None]
    total = int(max(cov_ends)) if cov_ends else int(min(durations))

    per_sec = np.full((total, K), -np.inf, dtype=np.float32)
    for k, (env, esr) in enumerate(envs):
        per_sec[:, k] = _lift_to_reference(env, esr, deltas[k], total)

    coverage = [_coverage_from_sidecar(p, total) for p in inputs]
    if coverage_flags:
        overrides = _parse_coverage_flag(coverage_flags, K, total)
        for v in coverage_flags:
            k = int(v.split(":")[0])
            coverage[k] = overrides[k]

    if verbose:
        print("Cameras (%d):" % K)
        for k, p in enumerate(inputs):
            s, e = coverage[k]
            print("  cam%d: %s  coverage [%.1f .. %.1f]s" % (k, p.name, s, e))

    finite = np.where(np.isfinite(per_sec), per_sec, np.nan)
    if K > 1:
        scores = np.full_like(per_sec, -np.inf)
        with warnings.catch_warnings():  # all-nan seconds -> nan, handled below
            warnings.simplefilter("ignore", RuntimeWarning)
            for k in range(K):
                others = np.nanmean(np.delete(finite, k, axis=1), axis=1)
                diff = finite[:, k] - others
                scores[:, k] = np.where(np.isfinite(diff), diff, -np.inf)
    else:
        scores = per_sec.copy()

    if audio_source is None:
        spread = []
        for k in range(K):
            v = finite[:, k]
            v = v[np.isfinite(v)]
            spread.append(0.0 if len(v) == 0 else
                          float(np.percentile(v, 90) - np.percentile(v, 10)))
        cov_pct = np.array([(coverage[k][1] - coverage[k][0]) / max(1, total)
                            for k in range(K)])
        audio_src = int(np.argmax(np.array(spread) + 0.5 * cov_pct))
    else:
        audio_src = audio_source

    if K == 1:
        seq = np.zeros(total, dtype=np.int32)
    elif mode == "rotation":
        seq = rotation_edit(scores, coverage, min_dwell=min_dwell,
                            max_dwell=max_dwell, seed=seed)
    else:
        seq = greedy_edit(scores, coverage, min_dwell=min_dwell,
                          max_dwell=max_dwell, switch_threshold=switch_threshold)
    edl = edl_from_seq(seq)

    return {
        "_about": ("EDL produced by polysync.edit.autoedit. Times are in the "
                   "reference timeline. deltas[k] is the per-input offset; "
                   "render scripts apply ffmpeg -itsoffset deltas[k] so they "
                   "read original (un-trimmed) files."),
        "schema_version": SCHEMA_VERSION,
        "inputs": [str(p) for p in inputs],
        "deltas": [float(d) for d in deltas],
        "duration_sec": total,
        "audio_source": audio_src,
        "coverage": [list(c) for c in coverage],
        "edl": edl,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(prog="polysync edit",
                                 description="Build a multicam auto-edit EDL.")
    ap.add_argument("inputs", type=Path, nargs="+",
                    help="Synced video files (camera 0, 1, ...)")
    ap.add_argument("--audio-source", type=int, default=None,
                    help="Cam index to use as master audio (default: highest "
                         "dynamic-range covered cam)")
    ap.add_argument("--mode", choices=["rotation", "greedy"], default="rotation")
    ap.add_argument("--min-dwell", type=int, default=8)
    ap.add_argument("--max-dwell", type=int, default=15)
    ap.add_argument("--switch-threshold", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--coverage", action="append", default=None,
                    help="Override per-cam coverage CAM:START:END (repeatable)")
    ap.add_argument("--out", type=Path, required=True, help="output EDL json")
    args = ap.parse_args(argv)

    plan = build_edl(
        args.inputs, mode=args.mode, audio_source=args.audio_source,
        min_dwell=args.min_dwell, max_dwell=args.max_dwell,
        switch_threshold=args.switch_threshold, seed=args.seed,
        coverage_flags=args.coverage,
    )
    args.out.write_text(json.dumps(plan, indent=2))
    edl, total = plan["edl"], plan["duration_sec"]
    print("\nEDL: %d segments; audio_source=cam%d; saved %s"
          % (len(edl), plan["audio_source"], args.out))
    counts = {}
    for row in edl:
        counts[row["cam"]] = counts.get(row["cam"], 0) + (row["end"] - row["start"])
    for k, dur in sorted(counts.items()):
        print("  cam%d: %.0fs on screen (%.0f%%)" % (k, dur, 100 * dur / total))


if __name__ == "__main__":
    main()
