#!/usr/bin/env python3
"""Replay a captured pi agent run against an OpenAI-compatible server.

Standalone: stdlib only. A corpus is a directory of requests-*.jsonl files
(one file per pi process; most corpora have exactly one).

    python3 bench-replay.py <corpus-dir> --base-url http://127.0.0.1:8000 --label my-config
    python3 bench-replay.py <corpus-dir> --model my-org/my-model ...   # if your server
                                                                       # names the model differently

FORMAT — each line of a requests-*.jsonl file:

    {"t": <epoch ms when sent>, "pid": <process id>, "payload": { ... }}

`payload` is the exact JSON body pi POSTed to the server: `model`, `messages`
(the full conversation so far — system prompt, task, every assistant reply,
every tool result), `tools`, and generation settings. Because the whole
history is resent each turn, the LAST request's `messages` list is the run's
complete transcript. Notes: `assistant.tool_calls[].function.arguments` is a
JSON-encoded string; there are no token counts in the captures. Occasional
`{"t":..., "response":...}` lines are internal bookkeeping — skip them, as
this tool does.

REPLAY — strictly sequential: requests are sent one at a time in original
order, each starting when the previous response finishes. Fresh generations
are discarded (the frozen histories keep the input identical across servers);
what is measured, per request and in aggregate, is time-to-first-token and
decode tokens/sec on YOUR server. Run one replay at a time and warm the
server with a couple of throwaway requests first.
"""

import argparse
import glob
import json
import os
import sys
import time
import urllib.request


def load_capture(cap_dir: str) -> list[dict]:
    reqs = []
    for f in glob.glob(os.path.join(cap_dir, "requests-*.jsonl")):
        for line in open(f):
            line = line.strip()
            if line:
                rec = json.loads(line)
                # skip bookkeeping lines; only "payload" lines are requests
                if "payload" in rec:
                    reqs.append(rec)
    reqs.sort(key=lambda r: r["t"])
    if not reqs:
        sys.exit(f"no requests-*.jsonl in {cap_dir}")
    return reqs


def replay_one(base_url: str, payload: dict, timeout: float,
               model: str | None = None, api_key: str = "local") -> dict:
    body = dict(payload)
    if model:
        body["model"] = model
    body["stream"] = True
    so = dict(body.get("stream_options") or {})
    so["include_usage"] = True
    body["stream_options"] = so
    # print out the raw message
    #print(f"replay_one: {json.dumps(body, indent=2)}", file=sys.stderr, flush=True)
    req = urllib.request.Request(
        f"{base_url}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"},
    )
    start = time.monotonic()
    ttft = None
    usage = None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw in resp:
                if not raw.startswith(b"data: "):
                    continue
                data = raw[6:].strip()
                if data == b"[DONE]":
                    break
                chunk = json.loads(data)
                if ttft is None and any(
                    (c.get("delta") or {}).get("content")
                    or (c.get("delta") or {}).get("reasoning_content")
                    or (c.get("delta") or {}).get("tool_calls")
                    for c in chunk.get("choices", [])
                ):
                    ttft = time.monotonic() - start
                if chunk.get("usage"):
                    usage = chunk["usage"]
    except Exception as e:
        return {"error": str(e), "wall": time.monotonic() - start}
    wall = time.monotonic() - start
    out = (usage or {}).get("completion_tokens") or 0
    inp = (usage or {}).get("prompt_tokens") or 0
    return {"wall": wall, "ttft": ttft, "in": inp, "out": out}


def main() -> None:
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("capture_dir")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--label", default="replay")
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--model", default=None,
                    help="override the model name pinned in the captured payloads")
    ap.add_argument("--api-key", default="local")
    ap.add_argument("--out-dir", default=None,
                    help="result dir (default: traces/bench if it exists, else .)")
    args = ap.parse_args()
    if args.out_dir is None:
        args.out_dir = "traces/bench" if os.path.isdir("traces/bench") else "."

    reqs = load_capture(args.capture_dir)
    print(f"replaying {len(reqs)} requests from {args.capture_dir} "
          f"against {args.base_url} (sequential)", file=sys.stderr, flush=True)
    results: list[dict | None] = [None] * len(reqs)

    def run(i: int, r: dict) -> None:
        res = replay_one(args.base_url, r["payload"], args.timeout,
                         args.model, args.api_key)
        results[i] = res
        if "error" in res:
                line = f"error: {res['error'][:60]}"
        else:
            dec = ((res["out"] - 1) / (res["wall"] - res["ttft"])
                   if res["ttft"] is not None and res["out"] > 1
                   and res["wall"] > res["ttft"] else None)
            line = (f"in {res['in']:>6} tok  out {res['out']:>5} tok  "
                    f"ttft {res['ttft']:.2f}s  " if res["ttft"] is not None else
                    f"in {res['in']:>6} tok  out {res['out']:>5} tok  ttft   -    ")
            line += f"wall {res['wall']:5.1f}s"
            if dec:
                line += f"  {dec:5.1f} tok/s"
        print(f"[{i+1:>3}/{len(reqs)}] {line}",
              file=sys.stderr, flush=True)

    start = time.monotonic()
    for i, r in enumerate(reqs):
        run(i, r)
    total_wall = time.monotonic() - start

    ok = [r for r in results if r and "error" not in r]
    errs = [r for r in results if r and "error" in r]
    gen = sum(r["out"] for r in ok)
    ttfts = sorted(r["ttft"] for r in ok if r["ttft"] is not None)
    decode = [
        (r["out"] - 1) / (r["wall"] - r["ttft"])
        for r in ok
        if r["ttft"] is not None and r["out"] > 1 and r["wall"] > r["ttft"]
    ]

    summary = {
        "label": args.label,
        "mode": "sequential",
        "requests": len(reqs),
        "errors": len(errs),
        "total_wall_s": round(total_wall, 1),
        "generated_tokens": gen,
        "workload_tok_s": round(gen / total_wall, 2),
        "mean_ttft_s": round(sum(ttfts) / len(ttfts), 2) if ttfts else None,
        "p90_ttft_s": round(ttfts[int(0.9 * (len(ttfts) - 1))], 2) if ttfts else None,
        "mean_decode_tok_s": round(sum(decode) / len(decode), 1) if decode else None,
        "per_request": results,
    }
    os.makedirs(args.out_dir, exist_ok=True)
    out_path = os.path.join(args.out_dir, f"replay_{args.label}.json")
    json.dump(summary, open(out_path, "w"), indent=2)

    for k in ("label", "mode", "requests", "errors", "total_wall_s",
              "generated_tokens", "workload_tok_s", "mean_ttft_s",
              "p90_ttft_s", "mean_decode_tok_s"):
        print(f"{k}: {summary[k]}")
    print(f"saved: {out_path}")
    if errs:
        print(f"first error: {errs[0]['error']}", file=sys.stderr)

if __name__ == "__main__":
    main()
