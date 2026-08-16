#!/usr/bin/env python3
"""Parse a pi session file, expanding subagent transcripts.

pi's subagent extension spawns children with --no-session (see the pinned
package's examples/extensions/subagent/index.ts), so they leave no session file
of their own. Their full transcripts survive only nested inside the parent's
tool result, under details.results[*].messages -- present in the data, but
rendered by nothing: pi's HTML exporter special-cases only details.diff, and
Harbor's viewer reads the same collapsed tool result.

This unpacks them, and can re-emit each child as a standalone pi session file
that `pi --export` and `pi --session` accept like any other.

Usage:
    parse_pi_session.py [session.jsonl] [--emit-children DIR]

Default session: newest under ~/.pi/agent/sessions/<project>/.
"""

import json
import sys
import collections
from datetime import datetime, timezone
from pathlib import Path

SESSIONS = Path.home() / ".pi/agent/sessions"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def newest():
    files = [f for d in SESSIONS.iterdir() if d.is_dir() for f in d.glob("*.jsonl")]
    if not files:
        sys.exit(f"no pi sessions under {SESSIONS}")
    return max(files, key=lambda f: f.stat().st_mtime)


def when(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc) if ms else None


def human(n):
    n = n or 0
    return f"{n/1000:.1f}k" if n >= 1000 else str(n)


def blocks(msg, kind):
    return [c for c in msg.get("content", []) if c.get("type") == kind]


def text_of(msg):
    return " ".join(c.get("text", "") for c in blocks(msg, "text"))


def tally(msgs):
    """Tool histogram, bash commands and token usage for a flat message list."""
    tools, bash, usage = collections.Counter(), [], collections.Counter()
    for m in msgs:
        if m.get("role") != "assistant":
            continue
        for c in blocks(m, "toolCall"):
            tools[c["name"]] += 1
            if c["name"] == "bash":
                bash.append(c.get("arguments", {}).get("command", ""))
        u = m.get("usage") or {}
        for k in ("input", "output", "cacheRead", "cacheWrite", "reasoning"):
            usage[k] += u.get(k, 0)
    return tools, bash, usage


def final_text(msgs):
    for m in reversed(msgs):
        if m.get("role") == "assistant" and text_of(m).strip():
            return " ".join(text_of(m).split())
    return ""


# --------------------------------------------------------------------------
# scan
# --------------------------------------------------------------------------

def scan(path):
    path = Path(path)
    recs = [json.loads(l) for l in open(path) if l.strip()]
    head = next(r for r in recs if r.get("type") == "session")
    mrecs = [r for r in recs if r.get("type") == "message"]
    flat = [r["message"] for r in mrecs]

    s = {
        "path": path, "size": path.stat().st_size, "records": len(recs),
        "head": head, "model": None, "thinking": None,
        "flat": flat,
    }
    for r in recs:
        if r.get("type") == "model_change":
            s["model"] = f"{r['provider']}/{r['modelId']}"
        if r.get("type") == "thinking_level_change":
            s["thinking"] = r.get("level", r.get("thinkingLevel"))

    # tree shape. The header's `id` is the session UUID, not a tree node --
    # excluding it, or it reads as a second leaf and fakes a branch.
    ids = {r["id"] for r in recs if "id" in r and r.get("type") != "session"}
    parents = [r["parentId"] for r in recs if r.get("parentId")]
    kids = collections.Counter(parents)
    s["leaves"] = len(ids - set(parents))
    s["forks"] = {k: v for k, v in kids.items() if v > 1}
    s["roles"] = collections.Counter(m["role"] for m in flat)

    stamps = [m.get("timestamp") for m in flat if m.get("timestamp")]
    s["span_min"] = ((when(max(stamps)) - when(min(stamps))).total_seconds() / 60
                     if stamps else 0)
    s["started"] = head.get("timestamp")

    s["prompts"] = [
        (m.get("timestamp"),
         ("(!) $ " + m["command"]) if m["role"] == "bashExecution" else text_of(m))
        for m in flat if m["role"] in ("user", "bashExecution")
    ]

    s["tools"], s["bash"], s["usage"] = tally(flat)

    # tool latency: toolCall -> matching toolResult
    call_at, call_name = {}, {}
    for m in flat:
        if m["role"] == "assistant":
            for c in blocks(m, "toolCall"):
                call_at[c["id"]] = m.get("timestamp")
                call_name[c["id"]] = c["name"]
    lat = []
    for m in flat:
        if m["role"] == "toolResult" and m.get("toolCallId") in call_at:
            lat.append(((m.get("timestamp", 0) - call_at[m["toolCallId"]]) / 1000,
                        call_name[m["toolCallId"]], bool(m.get("isError"))))
    s["latency"] = sorted(lat, key=lambda x: x[0], reverse=True)

    # subagent dispatches
    s["dispatches"] = []
    ct = collections.Counter()
    for m in flat:
        if m.get("toolName") != "subagent":
            continue
        det = m.get("details") or {}
        children = []
        for r in det.get("results", []):
            cm = r.get("messages", [])
            ctools, cbash, _ = tally(cm)
            u = r.get("usage", {})
            children.append({
                "agent": r.get("agent"), "task": r.get("task", ""),
                "exit": r.get("exitCode"), "model": r.get("model"),
                "turns": u.get("turns"), "usage": u, "messages": cm,
                "tools": ctools, "bash": cbash, "stderr": r.get("stderr", ""),
                "final": final_text(cm),
            })
            ct["messages"] += len(cm)
            ct["tools"] += sum(ctools.values())
            ct["input"] += u.get("input", 0)
            ct["output"] += u.get("output", 0)
        s["dispatches"].append({
            "ts": m.get("timestamp"), "mode": det.get("mode"),
            "scope": det.get("agentScope"), "children": children,
        })
    s["child_usage"] = ct
    return s


# --------------------------------------------------------------------------
# emit standalone child sessions
# --------------------------------------------------------------------------

def emit_children(s, out_dir):
    """Write each child transcript as its own pi session file. Returns paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    head, written, n = s["head"], [], 0
    for d in s["dispatches"]:
        for c in d["children"]:
            stem = f"{when(d['ts']):%Y-%m-%dT%H-%M-%S}_{c['agent']}_{n}"
            out = out_dir / f"{stem}.jsonl"
            with open(out, "w") as fh:
                fh.write(json.dumps({
                    "type": "session", "version": head["version"],
                    "id": head["id"], "timestamp": head["timestamp"],
                    "cwd": head["cwd"],
                }) + "\n")
                prev = None
                for i, m in enumerate(c["messages"]):
                    rid = f"{stem[-10:]}{i:04d}"
                    fh.write(json.dumps({
                        "type": "message", "id": rid, "parentId": prev,
                        "timestamp": (when(m.get("timestamp")) or
                                      datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z"),
                        "message": m,
                    }) + "\n")
                    prev = rid
            written.append(out)
            n += 1
    return written


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def report(s, out=sys.stdout):
    p = lambda *a: print(*a, file=out)
    p(f"# {s['path'].name}   ({s['size']/1024:.0f} KB, {s['records']} records)")
    p(f"  session  {s['head']['id']}  (format v{s['head']['version']})")
    p(f"  cwd      {s['head']['cwd']}")
    p(f"  started  {s['started']}")
    p(f"  model    {s['model']}")
    p(f"  thinking {s['thinking']}")
    p(f"  shape    {len(s['flat'])} messages, {s['leaves']} leaf/leaves"
      + (f", {len(s['forks'])} fork point(s) — rewound/edited" if s["forks"]
         else ", linear (no rewinds)"))
    p(f"  roles    " + ", ".join(f"{v} {k}" for k, v in s["roles"].most_common()))
    p(f"  span     {s['span_min']:.1f} min")

    p("\n## Prompts")
    for ts, txt in s["prompts"]:
        p(f"  [{when(ts):%H:%M:%S}] {' '.join(txt.split())[:95]}")

    p(f"\n## Parent tool calls ({sum(s['tools'].values())})")
    for n, c in s["tools"].most_common():
        p(f"  {c:3d}  {n}")
    if s["latency"]:
        lats = [d for d, _, _ in s["latency"]]
        p(f"  wall-clock {sum(lats):.0f}s total, median {sorted(lats)[len(lats)//2]:.1f}s")
        for d, n, err in s["latency"][:4]:
            p(f"    {d:7.1f}s  {n}{'  (error)' if err else ''}")

    if s["bash"]:
        p(f"\n## Parent bash ({len(s['bash'])})")
        for b in s["bash"]:
            p(f"  $ {' '.join(b.split())[:92]}")

    if not s["dispatches"]:
        p("\n## Subagents\n  none dispatched")
    else:
        p(f"\n## Subagents — {len(s['dispatches'])} dispatch(es)")
    for d in s["dispatches"]:
        p(f"\n  dispatch [{when(d['ts']):%H:%M:%S}] mode={d['mode']} "
          f"scope={d['scope']} -> {len(d['children'])} child process(es)")
        for c in d["children"]:
            u = c["usage"]
            p(f"\n    [{c['agent']}] exit={c['exit']} model={c['model']} turns={c['turns']}")
            p(f"      task: {' '.join(c['task'].split())[:88]}")
            p(f"      {len(c['messages'])} messages, {sum(c['tools'].values())} tool calls "
              f"({', '.join(f'{v} {k}' for k, v in c['tools'].most_common()) or 'none'})")
            p(f"      tokens: in {human(u.get('input'))} out {human(u.get('output'))} "
              f"ctx {human(u.get('contextTokens'))}")
            if c["stderr"].strip():
                p(f"      stderr: {c['stderr'].strip()[:80]}")
            for b in c["bash"][:6]:
                p(f"        $ {' '.join(b.split())[:80]}")
            if len(c["bash"]) > 6:
                p(f"        ... {len(c['bash'])-6} more bash call(s)")
            p(f"      returned: {c['final'][:88]}")

    u, ct = s["usage"], s["child_usage"]
    p(f"\n## Tokens")
    p(f"  parent:   in {human(u['input'])}  out {human(u['output'])}  "
      f"reasoning {human(u['reasoning'])}  cache r/w {human(u['cacheRead'])}/{human(u['cacheWrite'])}")
    if ct:
        p(f"  children: in {human(ct['input'])}  out {human(ct['output'])}   "
          f"({ct['messages']} messages, {ct['tools']} tool calls)")
        share = ct["output"] / max(u["output"] + ct["output"], 1)
        p(f"  -> {share*100:.0f}% of all generated tokens came from subagents, "
          f"invisible in every stock viewer")


def main():
    argv = sys.argv[1:]
    emit = None
    if "--emit-children" in argv:
        i = argv.index("--emit-children")
        emit = Path(argv[i + 1])
        del argv[i:i + 2]
    s = scan(Path(argv[0]) if argv else newest())
    report(s)
    if emit:
        written = emit_children(s, emit)
        if written:
            print(f"\n  {len(written)} child session(s) written to {emit}/")
            for w in written:
                print(f"    {w.name}")
            print("  open with:  pi --export <file>   |   pi --session <file>")
        else:
            print("\n  no subagent children to emit")


if __name__ == "__main__":
    main()
