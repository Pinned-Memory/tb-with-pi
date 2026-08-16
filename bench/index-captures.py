#!/usr/bin/env python3
"""Organize the capture collection into an index, grouped by task.

    python3 substrate/bench/index-captures.py            # print + write INDEX.md

Per corpus: processes, requests, active duration, delegation flag; task reward
joined from the originating harbor job when the corpus name carries its stamp
(<task>__<arm>__<job-stamp>). Repeat runs of one task group together.
"""

import glob
import json
import os
from collections import defaultdict

CAP = "traces/captures"


def corpus_stats(d: str) -> dict:
    reqs, t = 0, []
    pids = set()
    for f in glob.glob(os.path.join(d, "requests-*.jsonl")):
        pids.add(f.rsplit("-", 1)[1].split(".")[0])
        for line in open(f):
            if '"payload"' in line:
                reqs += 1
                t.append(json.loads(line)["t"])
    return {"procs": len(pids), "reqs": reqs,
            "dur": (max(t) - min(t)) / 1000 if len(t) > 1 else 0.0}


def _find_reward(o):
    if isinstance(o, dict):
        if isinstance(o.get("reward"), (int, float)):
            return o["reward"]
        for v in o.values():
            r = _find_reward(v)
            if r is not None:
                return r
    return None


def reward_of(task: str, stamp: str):
    for rj in glob.glob(f"jobs/{stamp}/{task}__*/result.json"):
        try:
            r = _find_reward(json.load(open(rj)))
            if r is not None:
                return r
        except (json.JSONDecodeError, OSError):
            pass
    return None


def main() -> None:
    groups = defaultdict(list)
    for d in sorted(glob.glob(os.path.join(CAP, "*"))):
        if not os.path.isdir(d):
            continue
        name = os.path.basename(d)
        parts = name.split("__")
        task, arm = parts[0], (parts[1] if len(parts) > 1 else "?")
        # the job stamp itself contains "__" (date__time), so rejoin the tail
        stamp = "__".join(parts[2:]) if len(parts) > 2 else None
        s = corpus_stats(d)
        s.update(name=name, arm=arm,
                 reward=reward_of(task, stamp) if stamp else None)
        groups[task].append(s)

    lines = ["# Capture collection index", ""]
    n_corpora = sum(len(v) for v in groups.values())
    n_reqs = sum(s["reqs"] for v in groups.values() for s in v)
    n_multi = sum(1 for v in groups.values() for s in v if s["procs"] > 1)
    lines += [f"{n_corpora} corpora · {len(groups)} tasks · {n_reqs} requests · "
              f"{n_multi} with subagent processes", "",
              "| task | runs | corpus | procs | reqs | active | reward |",
              "|---|---|---|---:|---:|---:|---:|"]
    for task in sorted(groups, key=lambda k: (-len(groups[k]), k)):
        for i, s in enumerate(groups[task]):
            rw = "" if s["reward"] is None else f"{s['reward']:.1f}"
            flag = " ⑂" if s["procs"] > 1 else ""
            lines.append(
                f"| {task if i == 0 else ''} | {len(groups[task]) if i == 0 else ''} "
                f"| {s['name']}{flag} | {s['procs']} | {s['reqs']} "
                f"| {s['dur']:.0f}s | {rw} |")
    out = "\n".join(lines) + "\n"
    open(os.path.join(CAP, "INDEX.md"), "w").write(out)

    print(f"{n_corpora} corpora, {len(groups)} tasks, {n_reqs} requests, "
          f"{n_multi} multi-process")
    multi = [(t, s) for t, v in groups.items() for s in v if s["procs"] > 1]
    rep = [(t, v) for t, v in groups.items() if len(v) > 1]
    print(f"tasks with repeat runs: {len(rep)}")
    for t, s in multi:
        print(f"  multi-process: {s['name']} ({s['procs']} procs, {s['reqs']} reqs)")
    print(f"index written: {CAP}/INDEX.md")


if __name__ == "__main__":
    main()
