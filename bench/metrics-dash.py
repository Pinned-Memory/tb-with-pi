#!/usr/bin/env python3
"""Live web dashboard for the vLLM server's Prometheus metrics.

    substrate/bench/metrics-dash.py            # then open http://localhost:8500
    substrate/bench/metrics-dash.py --port 9000 --target http://127.0.0.1:30000

vLLM ships no metrics UI. Its only built-in readout is the 10s stat lines in
the server log; /metrics is raw exposition text. The upstream answer is
Prometheus + Grafana via docker compose, which needs docker group membership,
and whose shipped dashboards graph latency/throughput but reference neither
vllm:spec_decode_* nor vllm:prefix_cache_* -- the two things this project
tunes for. This is the small local substitute: the MTP and prefix-cache
panels that Grafana would not have given us anyway.

Scraping happens here, server-side, not in the browser. vLLM enables CORS
only with --allowed-origins, so a page fetching :8000 directly would be
blocked and adding the flag means restarting a server that takes ~5 min to
load. This process polls, the page talks only to this process.

Every vllm:*_total is a counter, cumulative since server start, so the
lifetime average washes out exactly the variation worth seeing -- draft
acceptance swings 45-89% between windows while its lifetime figure sits flat.
Each sample is therefore differenced against the previous one and reported as
a windowed rate. History lives in this process, so a reload or a second tab
picks up the existing series instead of starting over.
"""

import argparse
import json
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Ring of derived points. 2s scrapes * 1800 = the last hour, ~1.5 MB resident.
HISTORY = deque(maxlen=1800)
HISTORY_LOCK = threading.Lock()
STATE = {"up": False, "error": None, "target": "", "spec_positions": 0}


def parse_prometheus(text):
    """Minimal exposition-format parser -> {(name, frozenset(labels)): float}.

    Only what this dashboard reads: no HELP/TYPE handling, no histograms
    beyond the _sum/_count pairs, no exemplars.
    """
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            head, value = line.rsplit(" ", 1)
        except ValueError:
            continue
        labels = {}
        if "{" in head:
            name, rest = head.split("{", 1)
            rest = rest.rstrip("}")
            for pair in rest.split(","):
                if "=" not in pair:
                    continue
                k, v = pair.split("=", 1)
                labels[k.strip()] = v.strip().strip('"')
        else:
            name = head
        try:
            out[(name, frozenset(labels.items()))] = float(value)
        except ValueError:
            continue
    return out


def get(sample, name, **labels):
    """First value of `name` whose labels are a superset of `labels`."""
    want = set(labels.items())
    for (n, lbls), v in sample.items():
        if n == name and want <= set(lbls):
            return v
    return None


def per_position(sample):
    """Accepted-token counters keyed by draft position, index order."""
    vals = {}
    for (n, lbls), v in sample.items():
        if n != "vllm:spec_decode_num_accepted_tokens_per_pos_total":
            continue
        pos = dict(lbls).get("position")
        if pos is not None:
            vals[int(pos)] = v
    return [vals[k] for k in sorted(vals)]


def ratio(num, den):
    """Windowed ratio, or None when the window saw no denominator traffic.

    None becomes a gap in the chart rather than a misleading 0 -- an idle
    server has no acceptance rate, it does not have an acceptance rate of
    zero.
    """
    if num is None or den is None or den <= 0:
        return None
    return num / den


def derive(prev, cur, dt):
    """One dashboard point from two consecutive scrapes."""
    def d(name, **lbls):
        a, b = get(prev, name, **lbls), get(cur, name, **lbls)
        if a is None or b is None:
            return None
        return max(0.0, b - a)  # counters reset to 0 if the server restarted

    d_drafts = d("vllm:spec_decode_num_drafts_total")
    d_draft_tok = d("vllm:spec_decode_num_draft_tokens_total")
    d_accept_tok = d("vllm:spec_decode_num_accepted_tokens_total")

    prev_pos, cur_pos = per_position(prev), per_position(cur)
    pos_rates = []
    if d_drafts:
        for i in range(min(len(prev_pos), len(cur_pos))):
            pos_rates.append(ratio(max(0.0, cur_pos[i] - prev_pos[i]), d_drafts))

    d_ttft_sum, d_ttft_n = d("vllm:time_to_first_token_seconds_sum"), d(
        "vllm:time_to_first_token_seconds_count")
    d_itl_sum, d_itl_n = d("vllm:inter_token_latency_seconds_sum"), d(
        "vllm:inter_token_latency_seconds_count")
    d_gen, d_prompt = d("vllm:generation_tokens_total"), d("vllm:prompt_tokens_total")

    accept = ratio(d_accept_tok, d_draft_tok)
    return {
        "t": time.time(),
        # Gauges: current value, no differencing.
        "running": get(cur, "vllm:num_requests_running"),
        "waiting": get(cur, "vllm:num_requests_waiting"),
        "kv": get(cur, "vllm:kv_cache_usage_perc"),
        # Windowed rates and ratios.
        "accept": accept,
        "accept_len": (1.0 + ratio(d_accept_tok, d_drafts)) if d_drafts else None,
        "pos": pos_rates,
        "prefix_hit": ratio(d("vllm:prefix_cache_hits_total"),
                            d("vllm:prefix_cache_queries_total")),
        "gen_tps": (d_gen / dt) if d_gen is not None and dt > 0 else None,
        "prompt_tps": (d_prompt / dt) if d_prompt is not None and dt > 0 else None,
        "ttft": ratio(d_ttft_sum, d_ttft_n),
        "itl": ratio(d_itl_sum, d_itl_n),
        "finished": d("vllm:request_success_total", finished_reason="stop"),
    }


def scrape_loop(target, interval):
    url = target.rstrip("/") + "/metrics"
    prev, prev_t = None, None
    while True:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                sample = parse_prometheus(r.read().decode("utf-8", "replace"))
            now = time.time()
            STATE.update(up=True, error=None)
            STATE["spec_positions"] = len(per_position(sample))
            if prev is not None and now > prev_t:
                point = derive(prev, sample, now - prev_t)
                with HISTORY_LOCK:
                    HISTORY.append(point)
            prev, prev_t = sample, now
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            STATE.update(up=False, error=str(e))
            prev = None  # force a fresh baseline; don't difference across a gap
        time.sleep(interval)


PAGE = r"""<!doctype html>
<meta charset="utf-8">
<title>vLLM metrics</title>
<style>
  :root {
    --bg:#0f1115; --panel:#171a21; --line:#252a34; --fg:#e6e9ef; --dim:#8b93a7;
    --accent:#5eb3f6; --good:#4ade80; --warn:#fbbf24; --bad:#f87171;
  }
  * { box-sizing:border-box; }
  body { margin:0; padding:20px; background:var(--bg); color:var(--fg);
         font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace; }
  h1 { font-size:15px; font-weight:600; margin:0 0 2px; }
  .sub { color:var(--dim); font-size:12px; margin-bottom:16px; }
  .dot { display:inline-block; width:8px; height:8px; border-radius:50%;
         background:var(--bad); margin-right:6px; vertical-align:middle; }
  .dot.up { background:var(--good); }
  .tiles { display:grid; gap:10px; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
           margin-bottom:14px; }
  .tile { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:10px 12px; }
  .tile .k { color:var(--dim); font-size:11px; text-transform:uppercase; letter-spacing:.05em; }
  .tile .v { font-size:22px; font-weight:600; margin-top:2px; }
  .tile .u { font-size:12px; color:var(--dim); font-weight:400; }
  .charts { display:grid; gap:10px; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); }
  .chart { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:10px 12px; }
  .chart .hd { display:flex; justify-content:space-between; align-items:baseline; margin-bottom:6px; }
  .chart .ttl { font-size:12px; color:var(--dim); }
  .chart .now { font-size:13px; font-weight:600; }
  svg { display:block; width:100%; height:90px; overflow:visible; }
  .empty { color:var(--dim); padding:40px 0; text-align:center; }
  .legend { font-size:11px; color:var(--dim); margin-top:4px; }
  .legend i { display:inline-block; width:8px; height:2px; margin:0 4px 0 10px; vertical-align:middle; }
</style>
<h1><span id="dot" class="dot"></span>vLLM metrics <span id="model" class="u"></span></h1>
<div class="sub" id="sub">connecting…</div>
<div class="tiles" id="tiles"></div>
<div class="charts" id="charts"></div>
<script>
const PCT = v => v == null ? null : v * 100;
// Each chart: series key(s), label, formatter, fixed y-domain when one exists.
const CHARTS = [
  {k:'accept',     t:'MTP draft acceptance',   f:v=>PCT(v), u:'%',     dom:[0,100], c:'--accent'},
  {k:'pos',        t:'Acceptance by position', f:v=>PCT(v), u:'%',     dom:[0,100], multi:true},
  {k:'prefix_hit', t:'Prefix cache hit rate',  f:v=>PCT(v), u:'%',     dom:[0,100], c:'--good'},
  {k:'accept_len', t:'Mean acceptance length', f:v=>v,      u:'tok',   c:'--warn'},
  {k:'gen_tps',    t:'Generation throughput',  f:v=>v,      u:'tok/s', c:'--accent'},
  {k:'prompt_tps', t:'Prompt throughput',      f:v=>v,      u:'tok/s', c:'--dim'},
  {k:'kv',         t:'KV cache usage',         f:v=>PCT(v), u:'%',     dom:[0,100], c:'--warn'},
  {k:'ttft',       t:'Mean TTFT',              f:v=>v,      u:'s',     c:'--bad'},
  {k:'itl',        t:'Mean inter-token latency', f:v=>v,    u:'s',     c:'--bad'},
];
const POS_COLORS = ['--accent','--warn','--bad','--good'];
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const fmt = (v,u) => v == null ? '—'
  : (u === '%' ? v.toFixed(1) : Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(2)) + (u==='%'?'%':'');

function path(vals, dom, w, h) {
  // vals may contain nulls (idle windows) -> emit separate subpaths, no
  // straight line bridging a gap that had no traffic.
  const nums = vals.filter(v => v != null);
  if (!nums.length) return '';
  let [lo, hi] = dom || [Math.min(...nums), Math.max(...nums)];
  if (hi - lo < 1e-9) { hi = lo + 1; lo -= 0; }
  const x = i => vals.length < 2 ? w : (i / (vals.length - 1)) * w;
  const y = v => h - ((v - lo) / (hi - lo)) * h;
  let d = '', pen = false;
  vals.forEach((v, i) => {
    if (v == null) { pen = false; return; }
    d += (pen ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1) + ' ';
    pen = true;
  });
  return d;
}

function draw(spec, hist) {
  const W = 600, H = 90;
  const series = spec.multi
    ? (() => {
        const n = Math.max(0, ...hist.map(p => (p.pos || []).length));
        return Array.from({length:n}, (_,i) => hist.map(p => spec.f((p.pos||[])[i] ?? null)));
      })()
    : [hist.map(p => spec.f(p[spec.k]))];
  const last = series.map(s => [...s].reverse().find(v => v != null) ?? null);
  const nowTxt = spec.multi
    ? last.map(v => fmt(v, spec.u)).join(' / ')
    : fmt(last[0], spec.u) + (spec.u && spec.u !== '%' ? ' ' + spec.u : '');
  const dom = spec.dom || (() => {
      const all = series.flat().filter(v => v != null);
      return all.length ? [Math.min(0, ...all), Math.max(...all)] : [0,1];
    })();
  const paths = series.map((s,i) =>
    `<path d="${path(s, dom, W, H)}" fill="none" stroke="${css(spec.multi ? POS_COLORS[i%4] : (spec.c||'--accent'))}" stroke-width="1.6"/>`
  ).join('');
  const legend = spec.multi
    ? `<div class="legend">${series.map((_,i)=>`<i style="background:${css(POS_COLORS[i%4])}"></i>pos ${i}`).join('')}</div>`
    : '';
  return `<div class="chart">
    <div class="hd"><span class="ttl">${spec.t}</span><span class="now">${nowTxt}</span></div>
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
      <line x1="0" y1="${H}" x2="${W}" y2="${H}" stroke="${css('--line')}"/>
      ${paths}
    </svg>${legend}</div>`;
}

async function tick() {
  let d;
  try { d = await (await fetch('api/stats')).json(); }
  catch (e) { document.getElementById('sub').textContent = 'dashboard unreachable'; return; }

  document.getElementById('dot').className = 'dot' + (d.up ? ' up' : '');
  document.getElementById('sub').textContent = d.up
    ? `${d.target} · ${d.history.length} samples · ${d.interval}s scrape`
    : `${d.target} unreachable — ${d.error || 'no response'}`;

  const h = d.history;
  if (!h.length) {
    document.getElementById('charts').innerHTML =
      '<div class="empty">waiting for the second scrape — rates need two samples</div>';
    return;
  }
  const l = h[h.length - 1];
  const back = k => { for (let i=h.length-1;i>=0;i--) if (h[i][k] != null) return h[i][k]; return null; };
  document.getElementById('tiles').innerHTML = [
    ['running',  l.running, ''], ['waiting', l.waiting, ''],
    ['kv cache', PCT(l.kv), '%'],
    ['acceptance', PCT(back('accept')), '%'],
    ['prefix hit', PCT(back('prefix_hit')), '%'],
    ['gen tok/s', back('gen_tps'), ''],
  ].map(([k,v,u]) => `<div class="tile"><div class="k">${k}</div>
      <div class="v">${v==null?'—':(u==='%'?v.toFixed(1):(Math.abs(v)>=100?v.toFixed(0):v.toFixed(v%1?2:0)))}<span class="u">${u}</span></div></div>`).join('');
  document.getElementById('charts').innerHTML =
    CHARTS.filter(s => s.k !== 'pos' || d.spec_positions > 0).map(s => draw(s, h)).join('');
}
tick(); setInterval(tick, 2000);
</script>
"""


class Handler(BaseHTTPRequestHandler):
    interval = 2.0

    def _send(self, code, body, ctype):
        body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/":
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif path == "/api/stats":
            with HISTORY_LOCK:
                hist = list(HISTORY)
            self._send(200, json.dumps({
                "up": STATE["up"], "error": STATE["error"], "target": STATE["target"],
                "spec_positions": STATE["spec_positions"],
                "interval": self.interval, "history": hist,
            }), "application/json")
        else:
            self._send(404, "not found\n", "text/plain")

    def log_message(self, *a):  # the vLLM log is noisy enough
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", default="http://127.0.0.1:8000",
                    help="server exposing /metrics (default: %(default)s)")
    ap.add_argument("--port", type=int, default=8500, help="dashboard port (default: %(default)s)")
    ap.add_argument("--host", default="127.0.0.1",
                    help="dashboard bind address; 0.0.0.0 to reach it from another machine")
    ap.add_argument("--interval", type=float, default=2.0,
                    help="scrape interval in seconds (default: %(default)s)")
    args = ap.parse_args()

    STATE["target"] = args.target
    Handler.interval = args.interval
    threading.Thread(target=scrape_loop, args=(args.target, args.interval), daemon=True).start()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    # flush=True: stdout is block-buffered when redirected to a log file, so
    # without it these two lines sit invisible in the buffer for the life of
    # the process and the log reads as empty.
    print(f"scraping {args.target}/metrics every {args.interval}s", flush=True)
    print(f"dashboard  http://{'localhost' if args.host == '127.0.0.1' else args.host}:{args.port}",
          flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
