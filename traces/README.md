# trace tooling

Turns pi session files into something you can actually read, and recovers the
subagent transcripts that no stock viewer renders.

## Why this exists

pi's subagent extension spawns each child with `--no-session`
(`examples/extensions/subagent/index.ts`), so children leave no session file.
Their transcripts are not lost — they are nested inside the parent's tool
result at `details.results[*].messages` — but nothing displays them: pi's HTML
exporter special-cases only `details.diff`, and Harbor's viewer reads the same
collapsed tool result. On the host sessions measured here that hides about half
of all generated tokens.

## Usage

```bash
# everything: host sessions + every Harbor trial -> traces/browsable/
python3 substrate/traces/collect.py

# one session, printed
python3 substrate/traces/parse_pi_session.py [session.jsonl]

# one session, with children re-emitted as standalone session files
python3 substrate/traces/parse_pi_session.py session.jsonl --emit-children ./kids
pi --export kids/<child>.jsonl      # standalone HTML
pi --session kids/<child>.jsonl     # reopen in the TUI
```

`collect.py` writes `traces/browsable/`: `index.html` (linked table of every
session and child), one HTML per transcript, `reports/*.txt`, and the extracted
child sessions under `children/`. Re-running regenerates it in place.

An extracted child is a real pi session file, so every tool that reads sessions
accepts it — no patch to pi or to the extension.

## Watching a dispatch live

Three vantage points, cheapest first.

**In the TUI — nothing to install.** At dispatch the tool renders
`subagent parallel (2 tasks) [user]` with each agent and a task preview. While
the children run, `onUpdate` fires on every child message, so their tool calls
stream into the result pane — last 10 items collapsed, **`ctrl+o`** expands to
the full transcript.

**Headless — the JSON event stream.** `tool_execution_update` carries
`partialResult.details`, i.e. the children's transcripts so far, so a dispatch
can be followed without waiting for it to return:

```bash
pi --print --mode json --model local-vllm/Qwen/Qwen3.8-27B "use scout to ..." \
  | python3 substrate/traces/watch_subagents.py

python3 substrate/traces/watch_subagents.py -f jobs/<job>/<task>/agent/pi.txt
```

**The process table — see the actual OS processes.** Each child is a separate
pi process, and its `--append-system-prompt` temp file names the agent:

```bash
watch -n1 'pgrep -af pi-subagent'
```

Parallel dispatch is capped at 8 tasks / 4 concurrent, and all of them share the
one vLLM server — so the server-side view (`vllm-serve.log`, running vs waiting
requests) is what tells you whether concurrency is buying anything.

## Files

| File | What |
|------|------|
| `parse_pi_session.py` | pi session -> stats + subagent expansion. `scan()` / `report()` / `emit_children()` are importable. |
| `collect.py` | Drives the above over every session on the box, renders HTML, writes the index. |
| `watch_subagents.py` | Live view of a dispatch from a pi JSON event stream (stdin or `-f`). |
| `parse_cc_session.py` | Same idea for Claude Code transcripts (`~/.claude/projects/<slug>/*.jsonl`). |

## Two format traps

- **Claude Code**: one API message is split across several records, one per
  content block, and `usage` is copied verbatim onto each. Group by
  `message.id` before summing or every token total inflates ~2.3x.
- **pi**: the session header's `id` is the session UUID, not a tree node. Count
  it as a node and it reads as a second leaf, faking a branch that isn't there.

## Snapshot hygiene

`traces/host-sessions/` holds point-in-time copies. The 21:14 session was
copied at 19:26 while it was still running: the snapshot has 16 lines, the live
file has 47, and the copy silently omits a later `worker` dispatch. Prefer
reading from `~/.pi/agent/sessions/`, or re-copy after a session ends.
