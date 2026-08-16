#!/usr/bin/env python3
"""Watch subagent activity live, from a pi JSON event stream.

pi's subagent tool reports progress through `onUpdate`, which the agent session
re-emits as `tool_execution_update` events carrying `partialResult.details` --
the children's transcripts *so far*. In `--mode json` those events land on
stdout, so a dispatch can be followed in real time without waiting for it to
finish.

    # live, headless
    pi --print --mode json --model local-vllm/Qwen/Qwen3.8-27B \
       "use scout to survey terminal-bench-2-1/tasks" \
       | python3 substrate/traces/watch_subagents.py

    # follow a run already in flight (Harbor writes the parent stream here)
    python3 substrate/traces/watch_subagents.py -f jobs/<job>/<task>/agent/pi.txt

In the interactive TUI you do not need this: the subagent tool renders its own
live progress, ctrl+o toggles full expansion.
"""

import json
import sys
import time
from pathlib import Path

C = {"dim": "\033[2m", "b": "\033[1m", "g": "\033[32m", "y": "\033[33m",
     "c": "\033[36m", "r": "\033[31m", "0": "\033[0m"}


def paint(s, *keys):
    if not sys.stdout.isatty():
        return s
    return "".join(C[k] for k in keys) + s + C["0"]


def brief(args, limit=68):
    if not isinstance(args, dict):
        return ""
    for k in ("command", "file_path", "path", "pattern", "query"):
        if k in args:
            return " ".join(str(args[k]).split())[:limit]
    return " ".join(json.dumps(args).split())[:limit]


class Watcher:
    """Tracks how much of each child's transcript has already been printed."""

    def __init__(self):
        self.seen = {}      # (dispatch id, child index) -> messages already shown
        self.started = {}

    def feed(self, ev):
        t = ev.get("type")
        if t == "tool_execution_start" and ev.get("toolName") == "subagent":
            self.on_start(ev)
        elif t == "tool_execution_update" and ev.get("toolName") == "subagent":
            self.on_update(ev)
        elif t == "tool_execution_end" and ev.get("toolName") == "subagent":
            self.on_end(ev)

    def on_start(self, ev):
        a = ev.get("args") or {}
        tasks = a.get("tasks") or a.get("chain") or ([a] if a.get("agent") else [])
        mode = "parallel" if a.get("tasks") else "chain" if a.get("chain") else "single"
        print(paint(f"\n▶ dispatch ({mode}) — {len(tasks)} child process(es)", "b", "y"))
        for t in tasks:
            print(paint(f"    {t.get('agent','?'):9s}", "c")
                  + paint(" ".join(str(t.get("task", "")).split())[:70], "dim"))
        self.started[ev.get("toolCallId")] = time.time()

    def _children(self, ev):
        pr = ev.get("partialResult") or ev.get("result") or {}
        det = pr.get("details") or {}
        return det.get("results", [])

    def on_update(self, ev):
        cid = ev.get("toolCallId")
        for i, r in enumerate(self._children(ev)):
            key = (cid, i)
            shown = self.seen.get(key, 0)
            msgs = r.get("messages", [])
            # a parallel dispatch often runs the same agent twice — index it,
            # or the two streams are indistinguishable
            label = f"{r.get('agent','?')}#{i}" if len(self._children(ev)) > 1 \
                else r.get("agent", "?")
            for m in msgs[shown:]:
                self.render(label, m)
            self.seen[key] = len(msgs)

    def render(self, agent, m):
        role = m.get("role")
        tag = paint(f"  [{agent}]", "c")
        if role == "assistant":
            for c in m.get("content", []):
                if c.get("type") == "toolCall":
                    print(f"{tag} {paint('→', 'dim')} {paint(c['name'], 'b')} "
                          f"{paint(brief(c.get('arguments')), 'dim')}")
                elif c.get("type") == "text" and c.get("text", "").strip():
                    first = " ".join(c["text"].split())[:74]
                    print(f"{tag} {paint(first, 'dim')}")
            u = m.get("usage") or {}
            if u.get("totalTokens"):
                print(f"{tag} {paint(f'  ctx {u['totalTokens']/1000:.1f}k', 'dim')}")
        elif role == "toolResult" and m.get("isError"):
            print(f"{tag} {paint('✗ ' + str(m.get('toolName')), 'r')}")

    def on_end(self, ev):
        cid = ev.get("toolCallId")
        dur = time.time() - self.started.get(cid, time.time())
        for r in self._children(ev):
            u = r.get("usage", {})
            ok = r.get("exitCode") == 0
            print(f"  {paint('✓' if ok else '✗', 'g' if ok else 'r')} "
                  f"{paint(r.get('agent','?'), 'b')} "
                  f"{len(r.get('messages', []))} msgs, {u.get('turns')} turns, "
                  f"in {u.get('input',0)/1000:.1f}k out {u.get('output',0)/1000:.1f}k")
        print(paint(f"  dispatch finished in {dur:.0f}s", "dim"))


def tail(path):
    """Yield lines from a file as it grows."""
    with open(path) as fh:
        while True:
            line = fh.readline()
            if line:
                yield line
            else:
                time.sleep(0.25)


def main():
    argv = sys.argv[1:]
    src = sys.stdin
    if "-f" in argv:
        src = tail(Path(argv[argv.index("-f") + 1]))
    w = Watcher()
    for line in src:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        w.feed(ev)
        sys.stdout.flush()


if __name__ == "__main__":
    main()
