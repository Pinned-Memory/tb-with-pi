#!/usr/bin/env python3
"""Reconstruct the process tree and concurrency timeline of a captured run.

    python3 substrate/bench/analyze-capture.py traces/captures/<corpus>

Each pi process (parent and every subagent) wrote its own requests-<pid>.jsonl.
This tool classifies processes (parent vs subagent), links each subagent to the
parent tool call that dispatched it, and draws an activity timeline so overlap
— which subagents actually ran concurrently — is visible at a glance.

Heuristics (validated on real corpora):
  * the process with the earliest first-request is the parent
  * subagent processes' first user message is the delegated task text
  * a child links to the parent request whose subagent tool-call task matches
    its first user message
Activity span per process = first request t .. last event t (response markers
included when the corpus has them; older corpora fall back to last request t,
which undercounts the final generation).
"""

import argparse
import glob
import json
import os
import sys


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(c.get("text", "") for c in content if isinstance(c, dict))
    return str(content)


def load(corpus: str) -> dict[int, dict]:
    procs = {}
    for f in sorted(glob.glob(os.path.join(corpus, "requests-*.jsonl"))):
        pid = int(os.path.basename(f).split("-")[1].split(".")[0])
        reqs, events = [], []
        for line in open(f):
            rec = json.loads(line)
            events.append(rec["t"])
            if "payload" in rec:
                reqs.append(rec)
        if reqs:
            procs[pid] = {"reqs": reqs, "start": min(events), "end": max(events)}
    if not procs:
        sys.exit(f"no capture files in {corpus}")
    return procs


def first_user_text(req: dict) -> str:
    for m in req["payload"]["messages"]:
        if m["role"] == "user":
            return text_of(m["content"]).strip()
    return ""


def dispatch_tasks(proc: dict) -> list[str]:
    """Task strings this process passed to the subagent tool."""
    tasks = []
    for r in proc["reqs"]:
        for m in r["payload"]["messages"]:
            if m["role"] != "assistant" or isinstance(m.get("content"), str):
                continue
            for c in m["content"] or []:
                if c.get("type") == "toolCall" and c.get("name", "").startswith("subagent"):
                    a = c.get("arguments") or {}
                    for t in [a.get("task")] + [
                        s.get("task") for s in (a.get("tasks") or a.get("chain") or []) if isinstance(s, dict)
                    ]:
                        if t:
                            tasks.append(str(t))
    return tasks


def print_transcript(proc: dict) -> None:
    """One process's conversation as it saw it — the subagent's point of view.

    The final request's message list is the full history; tool results are
    what the child's own tools returned to it.
    """
    msgs = proc["reqs"][-1]["payload"]["messages"]
    for m in msgs:
        role = m["role"]
        if role == "system":
            txt = text_of(m["content"])
            print(f"--- system ({len(txt)} chars) ---\n{txt[:400]}{'...' if len(txt) > 400 else ''}\n")
            continue
        if isinstance(m.get("content"), list):
            for c in m["content"]:
                if c.get("type") == "text" and c.get("text", "").strip():
                    print(f"[{role}] {c['text'][:500]}")
                elif c.get("type") == "toolCall":
                    print(f"[{role}] -> {c['name']}({json.dumps(c.get('arguments'))[:200]})")
        else:
            print(f"[{role}] {text_of(m.get('content'))[:500]}")
        print()
    # the final assistant reply lives in the parent's tool result, not here;
    # what this file shows is everything the child produced before it.


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus")
    ap.add_argument("--width", type=int, default=60, help="timeline columns")
    ap.add_argument("--transcript", type=int, metavar="PID",
                    help="print this process's conversation instead of the timeline")
    args = ap.parse_args()

    if args.transcript is not None:
        procs = load(args.corpus)
        if args.transcript not in procs:
            sys.exit(f"pid {args.transcript} not in corpus; have: {sorted(procs)}")
        print_transcript(procs[args.transcript])
        return

    procs = load(args.corpus)
    parent_pid = min(procs, key=lambda p: procs[p]["start"])
    t0 = procs[parent_pid]["start"]
    t_end = max(p["end"] for p in procs.values())
    span = max(1, t_end - t0)

    parent_tasks = dispatch_tasks(procs[parent_pid])

    def label(pid: int) -> str:
        if pid == parent_pid:
            return "parent"
        first = first_user_text(procs[pid]["reqs"][0])
        stripped = first.removeprefix("Task:").strip()
        for i, t in enumerate(parent_tasks):
            if stripped[:60] and stripped[:60] in t or t[:60] in first:
                return f"subagent#{i+1}"
        return "subagent?"

    print(f"{os.path.basename(os.path.normpath(args.corpus))}: "
          f"{len(procs)} processes, {sum(len(p['reqs']) for p in procs.values())} requests, "
          f"{span/1000:.0f}s wall")
    print()
    W = args.width
    for pid in sorted(procs, key=lambda p: procs[p]["start"]):
        p = procs[pid]
        a = int((p["start"] - t0) / span * W)
        b = max(a + 1, int((p["end"] - t0) / span * W))
        bar = " " * a + "█" * (b - a) + " " * (W - b)
        task = "" if pid == parent_pid else f'  "{first_user_text(p["reqs"][0])[:50]}..."'
        print(f"{label(pid):>11} {pid:>7} |{bar}| {len(p['reqs'])} reqs{task}")

    # concurrency profile: how many processes active per timeline column
    print()
    counts = []
    for col in range(W):
        t = t0 + span * col / W
        counts.append(sum(1 for p in procs.values() if p["start"] <= t <= p["end"]))
    peak = max(counts)
    print(f"{'active':>11} {'':>7} |{''.join(str(min(c,9)) for c in counts)}| peak concurrency: {peak}")
    if peak == 1:
        print("\nno overlap: subagents ran strictly sequentially (chain-style)")
    else:
        print(f"\noverlap detected: up to {peak} processes ran concurrently")


if __name__ == "__main__":
    main()
