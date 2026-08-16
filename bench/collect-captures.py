#!/usr/bin/env python3
"""Harvest request-capture corpora from Harbor job dirs into traces/captures/.

    python3 substrate/bench/collect-captures.py jobs/2026-08-16__* [--arm subagents]

Each trial that ran with --ak capture=true has agent/pi-capture/*.jsonl.
Corpora are copied to traces/captures/<task>__<arm>__<job-stamp>/ so repeated
runs of the same task never collide and the generating config stays legible.
Idempotent: existing destination dirs are left untouched.
"""

import argparse
import glob
import os
import shutil
import sys


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job_dirs", nargs="+", help="harbor job dirs (jobs/<stamp>)")
    ap.add_argument("--arm", default="subagents", help="config label baked into dir names")
    ap.add_argument("--out", default="traces/captures")
    args = ap.parse_args()

    copied = skipped = empty = 0
    for job in args.job_dirs:
        stamp = os.path.basename(os.path.normpath(job))
        for cap in glob.glob(os.path.join(job, "*", "agent", "pi-capture")):
            files = glob.glob(os.path.join(cap, "requests-*.jsonl"))
            if not files:
                empty += 1
                continue
            trial = os.path.basename(os.path.dirname(os.path.dirname(cap)))
            task = trial.rsplit("__", 1)[0]  # strip harbor's trial suffix
            dst = os.path.join(args.out, f"{task}__{args.arm}__{stamp}")
            if os.path.isdir(dst):
                skipped += 1
                continue
            os.makedirs(dst)
            for f in files:
                shutil.copy2(f, dst)
            copied += 1

    total = len(glob.glob(os.path.join(args.out, "*", "requests-*.jsonl")))
    dirs = len([d for d in glob.glob(os.path.join(args.out, "*")) if os.path.isdir(d)])
    print(f"copied {copied} corpora ({skipped} already present, {empty} without captures)")
    print(f"collection now: {dirs} corpora in {args.out}")
    if copied == 0 and skipped == 0:
        sys.exit("nothing found — did the runs use --ak capture=true?")


if __name__ == "__main__":
    main()
