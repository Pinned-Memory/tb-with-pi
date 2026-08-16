#!/usr/bin/env python3
"""Collect every pi trace on this box into one browsable directory.

For each session -- host sessions under ~/.pi/agent/sessions/, and the
per-trial sessions Harbor pulls out of the sandbox into
jobs/<job>/<task>/agent/pi/sessions/ -- this:

  1. writes a text report (parse_pi_session.report)
  2. extracts any subagent children into standalone session files
  3. renders parent and children to standalone HTML via `pi --export`
  4. links the lot from an index.html

Usage:
    python3 substrate/traces/collect.py [--out traces/browsable] [--no-html]
"""

import html
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse_pi_session import scan, report, emit_children, human, when  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
HOST_SESSIONS = Path.home() / ".pi/agent/sessions"
PI = "pi"


def discover():
    """(label, source-kind, path) for every pi session file we can find."""
    found = []
    for d in sorted(HOST_SESSIONS.glob("*")):
        if not d.is_dir():
            continue
        # the dir name is the project cwd with / replaced by -
        proj = d.name.strip("-").replace("--", "/") or "host"
        for f in sorted(d.glob("*.jsonl")):
            found.append((f"host: {proj.split('/')[-1]}", "host", f))
    for f in sorted(REPO.glob("jobs/*/*/agent/pi/sessions/*.jsonl")):
        job = f.parents[4].name           # jobs/<job>/<task>/agent/pi/sessions
        task = f.parents[3].name
        found.append((f"trial: {task}  ({job})", "harbor", f))
    return found


def export_html(session_path, out_dir):
    """`pi --export` writes pi-session-<stem>.html into cwd. Returns the name."""
    expected = f"pi-session-{session_path.stem}.html"
    r = subprocess.run([PI, "--export", str(session_path.resolve())],
                       cwd=out_dir, capture_output=True, text=True, timeout=300)
    if r.returncode != 0 or not (out_dir / expected).exists():
        print(f"    ! export failed: {(r.stderr or r.stdout).strip()[:120]}")
        return None
    return expected


def main():
    argv = sys.argv[1:]
    out = REPO / "traces/browsable"
    if "--out" in argv:
        i = argv.index("--out")
        out = Path(argv[i + 1])
        del argv[i:i + 2]
    do_html = "--no-html" not in argv

    out.mkdir(parents=True, exist_ok=True)
    (out / "children").mkdir(exist_ok=True)
    (out / "reports").mkdir(exist_ok=True)

    sessions = discover()
    print(f"found {len(sessions)} pi session(s)\n")
    rows = []

    for label, kind, path in sessions:
        print(f"[{label}]  {path.name}")
        s = scan(path)

        rpt = out / "reports" / f"{path.stem}.txt"
        with open(rpt, "w") as fh:
            report(s, out=fh)
        print(f"    report  {rpt.relative_to(out)}")

        kids = emit_children(s, out / "children")
        for k in kids:
            print(f"    child   children/{k.name}")

        parent_html = export_html(path, out) if do_html else None
        if parent_html:
            print(f"    html    {parent_html}")
        child_html = []
        for k in kids:
            h = export_html(k, out / "children") if do_html else None
            if h:
                child_html.append((k, h))

        rows.append({
            "label": label, "kind": kind, "scan": s,
            "report": rpt.relative_to(out), "html": parent_html,
            "children": child_html,
        })
        print()

    write_index(out, rows)
    print(f"index -> {out/'index.html'}")


def write_index(out, rows):
    def esc(x):
        return html.escape(str(x))

    total_children = sum(len(r["children"]) or
                         sum(len(d["children"]) for d in r["scan"]["dispatches"])
                         for r in rows)
    parts = [f"""<!doctype html>
<meta charset="utf-8"><title>magpie pi traces</title>
<style>
 :root {{ color-scheme: light dark; }}
 body {{ font: 14px/1.5 ui-sans-serif,system-ui,sans-serif; margin: 2rem auto; max-width: 62rem; padding: 0 1rem; }}
 h1 {{ font-size: 1.4rem; margin-bottom: .2rem; }}
 .sub {{ opacity: .7; margin-bottom: 1.5rem; }}
 table {{ border-collapse: collapse; width: 100%; margin-bottom: 2rem; }}
 th, td {{ text-align: left; padding: .45rem .6rem; border-bottom: 1px solid rgba(128,128,128,.3); vertical-align: top; }}
 th {{ font-weight: 600; font-size: .8rem; text-transform: uppercase; letter-spacing: .04em; opacity: .7; }}
 td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
 .kid {{ opacity: .85; }}
 .kid td:first-child {{ padding-left: 2rem; }}
 code {{ font-size: .85em; opacity: .8; }}
 a {{ color: inherit; }}
</style>
<h1>magpie — pi agent traces</h1>
<div class="sub">{len(rows)} session(s), {total_children} subagent child process(es).
Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by
<code>substrate/traces/collect.py</code>.</div>
<table>
<tr><th>session</th><th>model</th><th class="num">msgs</th><th class="num">tools</th>
<th class="num">in</th><th class="num">out</th><th>view</th></tr>"""]

    for r in rows:
        s = r["scan"]
        u = s["usage"]
        links = []
        if r["html"]:
            links.append(f'<a href="{esc(r["html"])}">html</a>')
        links.append(f'<a href="reports/{esc(r["report"].name)}">report</a>')
        parts.append(
            f'<tr><td><b>{esc(r["label"])}</b><br><code>{esc(s["path"].name)}</code></td>'
            f'<td><code>{esc((s["model"] or "?").split("/")[-1])}</code></td>'
            f'<td class="num">{len(s["flat"])}</td>'
            f'<td class="num">{sum(s["tools"].values())}</td>'
            f'<td class="num">{human(u["input"])}</td>'
            f'<td class="num">{human(u["output"])}</td>'
            f'<td>{" · ".join(links)}</td></tr>')

        n = 0
        for d in s["dispatches"]:
            for c in d["children"]:
                cu = c["usage"]
                link = ""
                if n < len(r["children"]):
                    kpath, khtml = r["children"][n]
                    link = f'<a href="children/{esc(khtml)}">html</a>'
                parts.append(
                    f'<tr class="kid"><td>↳ <b>{esc(c["agent"])}</b> '
                    f'<code>{esc(" ".join(c["task"].split())[:60])}</code></td>'
                    f'<td><code>{esc((c["model"] or "?").split("/")[-1])}</code></td>'
                    f'<td class="num">{len(c["messages"])}</td>'
                    f'<td class="num">{sum(c["tools"].values())}</td>'
                    f'<td class="num">{human(cu.get("input"))}</td>'
                    f'<td class="num">{human(cu.get("output"))}</td>'
                    f'<td>{link}</td></tr>')
                n += 1

    parts.append("</table>")
    (out / "index.html").write_text("\n".join(parts))


if __name__ == "__main__":
    main()
