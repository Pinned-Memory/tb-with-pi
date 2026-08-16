#!/usr/bin/env python3
"""Parse a Claude Code session transcript (~/.claude/projects/<slug>/<uuid>.jsonl).

Usage: parse_cc_session.py [path.jsonl]   (default: newest in this project dir)
"""

import json
import sys
import collections
from datetime import datetime
from pathlib import Path

PROJ = Path.home() / ".claude/projects/-home-cc2869-research-magpie"


def load(p):
    return [json.loads(l) for l in open(p) if l.strip()]


def ts(r):
    t = r.get("timestamp")
    return datetime.fromisoformat(t.replace("Z", "+00:00")) if t else None


def human(n):
    return f"{n/1000:.1f}k" if n >= 1000 else str(n)


def main():
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else max(
        PROJ.glob("*.jsonl"), key=lambda f: f.stat().st_mtime
    )
    recs = load(p)
    stamped = [r for r in recs if r.get("timestamp")]
    t0, t1 = ts(stamped[0]), ts(stamped[-1])

    meta = next(r for r in recs if r["type"] == "user")
    print(f"# {p.name}   ({p.stat().st_size/1024:.0f} KB, {len(recs)} records)")
    print(f"  cwd      {meta.get('cwd')}")
    print(f"  branch   {meta.get('gitBranch') or '(not a git repo)'}")
    print(f"  client   claude-code {meta.get('version')} via {meta.get('entrypoint')}")
    title = next((r["aiTitle"] for r in reversed(recs) if r["type"] == "ai-title"), None)
    print(f"  title    {title!r}")
    print(f"  span     {t0:%H:%M:%S} -> {t1:%H:%M:%S}  ({(t1-t0).total_seconds()/60:.1f} min)")
    modes = {r.get("permissionMode") for r in recs if r["type"] == "permission-mode"}
    print(f"  perms    {', '.join(sorted(m for m in modes if m))}")

    # ---- turns -------------------------------------------------------------
    prompts = [r for r in recs if r["type"] == "user"
               and not r.get("toolUseResult") and not r.get("isMeta")
               and isinstance(r["message"].get("content"), str)]
    tool_results = [r for r in recs if r.get("toolUseResult")]
    asst = [r for r in recs if r["type"] == "assistant"]

    # One API message is split across several records (one per content block),
    # and `usage` is repeated verbatim on each. Group by message id, or every
    # token total comes out inflated by the number of blocks.
    by_msg = collections.OrderedDict()
    for r in asst:
        by_msg.setdefault(r["message"]["id"], []).append(r)

    print(f"\n## Turns")
    print(f"  {len(prompts)} user prompt(s), {len(by_msg)} assistant API messages "
          f"({len(asst)} records — one per content block), "
          f"{len(tool_results)} tool results")
    for i, r in enumerate(prompts, 1):
        txt = " ".join(r["message"]["content"].split())
        src = r.get("promptSource", "?")
        print(f"  {i}. [{ts(r):%H:%M:%S}] ({src}) {txt[:100]}")

    # ---- tool calls --------------------------------------------------------
    calls = {}          # tool_use id -> (name, input, timestamp)
    per_msg = []
    for mid, group in by_msg.items():
        blocks = [c for r in group for c in r["message"].get("content", [])
                  if c.get("type") == "tool_use"]
        if blocks:
            per_msg.append(len(blocks))
        for c, r in zip(blocks, [r for r in group
                                 for c2 in r["message"].get("content", [])
                                 if c2.get("type") == "tool_use"]):
            calls[c["id"]] = (c["name"], c.get("input", {}), ts(r))

    names = collections.Counter(v[0] for v in calls.values())
    print(f"\n## Tool calls  ({len(calls)} total)")
    for n, c in names.most_common():
        print(f"  {c:3d}  {n}")
    if per_msg:
        par = collections.Counter(per_msg)
        print("  batched per API message: " +
              ", ".join(f"{v} msg(s) issued {k} call(s)" for k, v in sorted(par.items())))

    # latency: tool_use timestamp -> matching result timestamp
    lat = []       # (seconds, tool name, input)
    lat_t = []     # same, plus invocation time — for the per-turn split
    for r in tool_results:
        content = r["message"].get("content")
        tid = None
        if isinstance(content, list):
            for c in content:
                if c.get("type") == "tool_result":
                    tid = c.get("tool_use_id")
        if tid in calls:
            d = (ts(r) - calls[tid][2]).total_seconds()
            lat.append((d, calls[tid][0], calls[tid][1]))
            lat_t.append((d, calls[tid][0], calls[tid][1], calls[tid][2]))
    if lat:
        lat.sort(key=lambda x: x[0], reverse=True)
        tot = sum(d for d, _, _ in lat)
        print(f"\n  tool wall-clock: {tot:.0f}s total, median {sorted(d for d,_,_ in lat)[len(lat)//2]:.1f}s")
        print("  slowest:")
        for d, n, inp in lat[:5]:
            desc = inp.get("description") or inp.get("command") or inp.get("file_path") or ""
            print(f"    {d:6.1f}s  {n:6s}  {' '.join(str(desc).split())[:66]}")

    # ---- cadence: where the wall clock goes --------------------------------
    # Split the session at each typed prompt; within a turn, time is either
    # blocked on a tool or spent generating. Idle time between a finished
    # answer and the next prompt is the user's, not the agent's.
    typed = [r for r in prompts if r.get("promptSource") == "typed"]
    if typed:
        print(f"\n## Cadence")
        bounds = [ts(r) for r in typed] + [t1]
        for i, r in enumerate(typed):
            start, end = bounds[i], bounds[i + 1]
            # tool calls whose invocation falls inside this turn
            tool_s = sum(d for d, _, _, when in lat_t if start <= when < end)
            # last assistant activity in this turn = end of agent work
            acts = [ts(x) for x in asst if start <= ts(x) < end]
            worked = (max(acts) - start).total_seconds() if acts else 0
            idle = (end - max(acts)).total_seconds() if acts and i + 1 < len(typed) else 0
            txt = " ".join(r["message"]["content"].split())[:48]
            print(f"  turn {i+1}: {worked/60:.1f} min working "
                  f"({tool_s:.0f}s in tools, {worked-tool_s:.0f}s generating)"
                  + (f", then {idle/60:.1f} min idle" if idle else "")
                  + f"   — {txt!r}")

    # ---- bash commands -----------------------------------------------------
    bash = [(t, i) for (n, i, t) in calls.values() if n == "Bash"]
    if bash:
        print(f"\n## Bash ({len(bash)})")
        for t, i in bash:
            print(f"  [{t:%H:%M:%S}] {' '.join(i.get('command','').split())[:95]}")

    # ---- tokens ------------------------------------------------------------
    agg = collections.Counter()
    for mid, group in by_msg.items():
        u = group[0]["message"].get("usage", {})   # identical across the group
        agg["input"] += u.get("input_tokens", 0)
        agg["output"] += u.get("output_tokens", 0)
        agg["cache_read"] += u.get("cache_read_input_tokens", 0)
        agg["cache_write"] += u.get("cache_creation_input_tokens", 0)
        agg["thinking"] += (u.get("output_tokens_details") or {}).get("thinking_tokens", 0)
    billed_in = agg["input"] + agg["cache_read"] + agg["cache_write"]
    print(f"\n## Tokens  (model {asst[0]['message']['model']}, effort {asst[0].get('effort')})")
    print(f"  input       {human(agg['input']):>8}   uncached")
    print(f"  cache read  {human(agg['cache_read']):>8}   {agg['cache_read']/billed_in*100:.1f}% of prompt tokens")
    print(f"  cache write {human(agg['cache_write']):>8}")
    print(f"  output      {human(agg['output']):>8}   of which {human(agg['thinking'])} thinking "
          f"({agg['thinking']/max(agg['output'],1)*100:.0f}%)")
    ctx = max((g[0]["message"]["usage"].get("cache_read_input_tokens", 0)
               + g[0]["message"]["usage"].get("cache_creation_input_tokens", 0))
              for g in by_msg.values())
    print(f"  peak context{human(ctx):>8}")

    # ---- output volume -----------------------------------------------------
    out_chars = sum(len(c.get("text", "")) for r in asst
                    for c in r["message"].get("content", []) if c.get("type") == "text")
    res_chars = 0
    for r in tool_results:
        tr = r["toolUseResult"]
        res_chars += len(json.dumps(tr)) if not isinstance(tr, str) else len(tr)
    print(f"\n## Volume")
    print(f"  assistant prose   {out_chars/1024:.0f} KB")
    print(f"  tool results      {res_chars/1024:.0f} KB   ({res_chars/max(out_chars,1):.1f}x the prose)")

    # ---- context injections ------------------------------------------------
    att = collections.Counter(r["attachment"].get("type") for r in recs
                              if r["type"] == "attachment" and isinstance(r.get("attachment"), dict))
    if att:
        print(f"\n## Injected attachments")
        for k, v in att.most_common():
            print(f"  {v:3d}  {k}")


if __name__ == "__main__":
    main()
