#!/usr/bin/env python3
"""Tiny localhost HTTP server that lets the dashboard's Refresh button trigger
a full sync from the browser. Runs on 127.0.0.1:8789, bound to loopback only.

Endpoints:
  GET  /ping      → {ok: true}          (dashboard uses this to detect availability)
  POST /refresh   → runs run_sync.sh, returns {ok, stdout, stderr}

CORS is restricted to the deployed dashboard origin plus local previews.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

PROJECT_DIR = Path(__file__).resolve().parent.parent
SYNC_SCRIPT = PROJECT_DIR / "run_sync.sh"
DB_PATH = PROJECT_DIR / "data" / "music.db"
AGGREGATES_DIR = PROJECT_DIR / "data" / "aggregates"
ANTHROPIC_KEY_FILE = PROJECT_DIR / "credentials" / "anthropic.key"
PORT = 8789

# Cheapest current Claude model. For a slightly smarter but ~4x pricier
# option, swap to "claude-haiku-4-5-20251001".
CLAUDE_MODEL = "claude-3-haiku-20240307"

# Safety rails on Claude usage. Combine with a spend cap on the Anthropic
# billing console for defence in depth.
ASK_MAX_COST_PER_DAY_USD = 0.50       # hard daily dollar cap
ASK_MAX_Q_CHARS = 400
ASK_LOG_FILE = PROJECT_DIR / "logs" / "ask.log"

# Claude Haiku 3 pricing (USD per million tokens).
_MODEL_IN_PRICE  = 0.25 / 1_000_000
_MODEL_OUT_PRICE = 1.25 / 1_000_000

ALLOWED_ORIGINS = {
    "https://benjaminbellman.github.io",
    "http://localhost:8788",
    "http://127.0.0.1:8788",
    f"http://127.0.0.1:{8789}",
    f"http://localhost:{8789}",
}

DASHBOARD_URL = "https://benjaminbellman.github.io/music-dashboard/"

STATUS_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Refreshing Music Dashboard…</title>
<style>
  :root { color-scheme: dark; }
  body { font-family: -apple-system, system-ui, "Segoe UI", sans-serif;
         background: #0b0814; color: #ede8f7; margin: 0; min-height: 100vh;
         display: grid; place-items: center; }
  main { max-width: 540px; padding: 2rem; text-align: center; }
  h1 { font-size: 1.6rem; margin: 0 0 0.4rem;
       background: linear-gradient(135deg,#a78bfa,#ec4899);
       -webkit-background-clip: text; background-clip: text; color: transparent; }
  p { color: #8a83a6; line-height: 1.55; margin: 0.5rem 0; }
  .spinner { width: 44px; height: 44px; margin: 1.5rem auto; border-radius: 50%;
             border: 3px solid #2a2440; border-top-color: #a78bfa;
             animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .status { font-variant-numeric: tabular-nums; color: #a78bfa; font-weight: 600; }
  pre { background: #120f1f; border: 1px solid #2a2440; padding: 0.85rem;
        text-align: left; border-radius: 8px; max-height: 240px;
        overflow: auto; font-size: 0.78rem; color: #8a83a6;
        white-space: pre-wrap; word-break: break-word; margin-top: 1rem; }
  a, button { color: #a78bfa; }
  .btn { display: inline-block; padding: 0.55rem 1.1rem;
         background: #1a1532; border: 1px solid #2a2440; color: #ede8f7;
         border-radius: 999px; text-decoration: none; cursor: pointer;
         font-family: inherit; font-size: 0.9rem; margin-top: 1rem; }
  .btn:hover { background: rgba(167,139,250,0.18); border-color: #a78bfa; color: #a78bfa; }
  .err { color: #f87171; }
  .ok  { color: #22c55e; }
</style>
</head>
<body>
<main>
  <h1>Refreshing your dashboard</h1>
  <div class="spinner" id="spinner"></div>
  <p id="status" class="status">Extracting Music library, enriching, rebuilding…</p>
  <p>This usually takes 2–3 minutes. You can close this tab if you want — the sync keeps running.</p>
  <pre id="log" hidden></pre>
  <a class="btn" id="back" href="__DASHBOARD_URL__" hidden>Back to dashboard</a>
</main>
<script>
const startedAt = Date.now();
const tick = setInterval(() => {
  const s = Math.floor((Date.now() - startedAt) / 1000);
  document.getElementById("status").textContent =
    `Extracting · enriching · rebuilding…  (${Math.floor(s/60)}m ${s%60}s elapsed)`;
}, 1000);

(async () => {
  try {
    const r = await fetch("/sync", { method: "POST" });
    const body = await r.json().catch(() => ({}));
    clearInterval(tick);
    document.getElementById("spinner").style.display = "none";
    if (r.ok && body.ok) {
      document.getElementById("status").innerHTML =
        '<span class="ok">✓ Synced.</span> GitHub Pages will redeploy in ~1 min — heading back…';
      document.getElementById("back").hidden = false;
      setTimeout(() => location.replace("__DASHBOARD_URL__"), 75000);
    } else {
      document.getElementById("status").innerHTML =
        '<span class="err">Sync failed.</span> Details below.';
      const log = document.getElementById("log");
      log.hidden = false;
      log.textContent = (body.stderr || body.error || "").trim() || JSON.stringify(body, null, 2);
      document.getElementById("back").hidden = false;
    }
  } catch (e) {
    clearInterval(tick);
    document.getElementById("spinner").style.display = "none";
    document.getElementById("status").innerHTML = '<span class="err">Network error: ' + e.message + '</span>';
    document.getElementById("back").hidden = false;
  }
})();
</script>
</body>
</html>
""".replace("__DASHBOARD_URL__", DASHBOARD_URL)

_lock = threading.Lock()


def _pending_artists() -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT p.artist, COUNT(t.track_id) AS tracks, SUM(t.plays) AS plays
        FROM artist_country_pending p
        LEFT JOIN tracks_current t ON t.primary_artist = p.artist
        GROUP BY p.artist
        ORDER BY -SUM(t.plays), p.artist
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _today_spent_usd() -> tuple[float, int]:
    """Return (total cost today in USD, number of /ask calls today) from ask.log."""
    if not ASK_LOG_FILE.exists():
        return 0.0, 0
    today = dt.date.today().isoformat()
    total = 0.0
    count = 0
    try:
        for line in ASK_LOG_FILE.read_text().splitlines():
            if not line.startswith(today):
                continue
            count += 1
            # Each line has a "cost=$<float>" field.
            for part in line.split("\t"):
                if part.startswith("cost=$"):
                    try:
                        total += float(part[6:])
                    except ValueError:
                        pass
    except Exception:  # noqa: BLE001
        pass
    return total, count


def _log_question(question: str, tokens_in: int, tokens_out: int) -> None:
    ASK_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    cost = tokens_in * _MODEL_IN_PRICE + tokens_out * _MODEL_OUT_PRICE
    line = (
        f"{dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()}\t"
        f"in={tokens_in}\tout={tokens_out}\tcost=${cost:.5f}\t"
        f"{question[:120].replace(chr(9), ' ').replace(chr(10), ' ')}\n"
    )
    with ASK_LOG_FILE.open("a") as f:
        f.write(line)


def _ask_claude(question: str) -> dict:
    """Send the user's question to Claude with library aggregates as context."""
    if not ANTHROPIC_KEY_FILE.exists():
        return {
            "error": (
                "No Anthropic API key configured. Put your key in "
                "`credentials/anthropic.key` (one line, no quotes) and try again."
            )
        }
    api_key = ANTHROPIC_KEY_FILE.read_text().strip()
    if not api_key:
        return {"error": "credentials/anthropic.key is empty."}

    # Guardrails
    if len(question) > ASK_MAX_Q_CHARS:
        return {"error": f"Question is too long ({len(question)} chars). Limit is {ASK_MAX_Q_CHARS}."}
    spent_today, asked_today = _today_spent_usd()
    if spent_today >= ASK_MAX_COST_PER_DAY_USD:
        return {
            "error": (
                f"Daily cost cap reached (${spent_today:.4f} of "
                f"${ASK_MAX_COST_PER_DAY_USD:.2f} spent today). "
                f"Edit ASK_MAX_COST_PER_DAY_USD in pipeline/refresh_server.py to raise it."
            )
        }

    # Build a compact context from the pre-computed aggregates.
    context_names = [
        "kpis", "top_artists", "country_plays", "genre_plays",
        "genre_year", "year_artist", "country_year", "month_year",
    ]
    context_parts = []
    for name in context_names:
        p = AGGREGATES_DIR / f"{name}.json"
        if p.exists():
            context_parts.append(f"### {name}.json\n{p.read_text()}")
    context = "\n\n".join(context_parts)

    try:
        import anthropic
    except ImportError:
        return {"error": "Python `anthropic` package not installed in .venv."}

    client = anthropic.Anthropic(api_key=api_key)
    system = (
        "You are an analyst for the user's personal Apple Music library. "
        "Use ONLY the JSON aggregates below to answer. Be concise, specific, "
        "and quote actual numbers. If the question isn't answerable from the "
        "data, say so plainly.\n\n"
        "Notes on the data:\n"
        "- All counts are lifetime plays as of the last sync.\n"
        "- 'year' in year_artist / genre_year / country_year means the year "
        "the song was ADDED to the library (proxy for 'when I was into it').\n"
        "- Artists in top_artists.by_*_count already include multi-artist "
        "credit: a song 'X & Y' contributes to both X and Y.\n"
        "- country_plays uses the artist's country from a manual ledger.\n\n"
        f"=== DATA ===\n{context}"
    )

    try:
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            system=system,   # Haiku 3 doesn't support prompt caching; pass plain string
            messages=[{"role": "user", "content": question}],
        )
        text = "".join(block.text for block in message.content if hasattr(block, "text"))
        tin, tout = message.usage.input_tokens, message.usage.output_tokens
        _log_question(question, tin, tout)
        cost = tin * _MODEL_IN_PRICE + tout * _MODEL_OUT_PRICE
        return {
            "answer": text,
            "model": message.model,
            "tokens_in": tin,
            "tokens_out": tout,
            "cost": cost,
            "asked_today": asked_today + 1,
            "spent_today": spent_today + cost,
            "cap_usd": ASK_MAX_COST_PER_DAY_USD,
        }
    except Exception as e:  # noqa: BLE001
        return {"error": f"Claude API error: {e}"}


def _save_country(artist: str, country: str) -> None:
    iso = country.strip().upper()
    if len(iso) != 2 or not iso.isalpha():
        raise ValueError(f"country must be 2 letters, got {country!r}")
    ts = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    conn = sqlite3.connect(str(DB_PATH))
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO artist_country (artist, country, source, updated) "
            "VALUES (?, ?, 'manual', ?)",
            (artist, iso, ts),
        )
        conn.execute("DELETE FROM artist_country_pending WHERE artist = ?", (artist,))
    conn.close()


def ask_page(question: str, answer_html: str, meta: str = "") -> str:
    q = question.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Ask — Music Dashboard</title>
<style>
  :root {{ color-scheme: dark; --accent:#a78bfa; --bg:#0b0814; --surface:#120f1f; --surface2:#1a1532; --border:#2a2440; --fg:#ede8f7; --muted:#8a83a6; }}
  body {{ font-family:-apple-system,system-ui,sans-serif; background:var(--bg); color:var(--fg); margin:0; padding:2.5rem 1rem; }}
  main {{ max-width:760px; margin:0 auto; }}
  h1 {{ font-size:1.6rem; margin:0 0 0.3rem;
        background: linear-gradient(135deg,#a78bfa,#ec4899);
        -webkit-background-clip: text; background-clip: text; color: transparent; }}
  .q {{ color: var(--muted); margin-bottom: 1.5rem; font-size: 0.95rem; }}
  .q em {{ color: var(--fg); font-style: normal; }}
  .answer {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-left: 3px solid var(--accent);
    border-radius: 10px;
    padding: 1.2rem 1.4rem;
    font-size: 1.04rem;
    line-height: 1.65;
    white-space: pre-wrap;
  }}
  .answer.err {{ border-left-color: #f87171; color: #fca5a5; }}
  .meta {{ color: var(--muted); font-size: 0.78rem; margin-top: 0.9rem; }}
  form {{ margin-top: 2rem; display: flex; gap: 0.5rem; }}
  input[type=text] {{ flex:1; background:var(--surface2); border:1px solid var(--border); color:var(--fg); padding:0.6rem 0.9rem; border-radius:8px; font-family:inherit; font-size:0.95rem; }}
  button {{ background:var(--accent); color:#0b0814; border:0; padding:0 1.1rem; border-radius:8px; font-weight:600; cursor:pointer; font-family:inherit; }}
  a.back {{ display:inline-block; margin-top: 1.25rem; color: var(--muted); text-decoration: none; font-size: 0.85rem; }}
  a.back:hover {{ color: var(--accent); }}
</style>
</head>
<body>
<main>
  <h1>Ask</h1>
  <div class="q"><em>{q}</em></div>
  <div class="answer">{answer_html}</div>
  {meta and f'<div class="meta">{meta}</div>' or ""}

  <form action="/ask" method="GET">
    <input type="text" name="q" placeholder="Ask another question…" autofocus />
    <button type="submit">Ask →</button>
  </form>
  <a class="back" href="https://benjaminbellman.github.io/music-dashboard/#insights">← Back to dashboard</a>
</main>
</body>
</html>
"""


ASSIGN_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Assign countries — Music Dashboard</title>
<style>
  :root { color-scheme: dark; --accent: #a78bfa; --bg: #0b0814; --surface: #120f1f; --surface2: #1a1532; --border: #2a2440; --fg: #ede8f7; --muted: #8a83a6; }
  body { font-family: -apple-system, system-ui, sans-serif; background: var(--bg); color: var(--fg); margin: 0; padding: 2rem 1rem; }
  main { max-width: 720px; margin: 0 auto; }
  h1 { font-size: 1.6rem; margin: 0 0 0.4rem; background: linear-gradient(135deg,#a78bfa,#ec4899); -webkit-background-clip: text; background-clip: text; color: transparent; }
  p.sub { color: var(--muted); margin: 0 0 1.5rem; }
  .row { display: grid; grid-template-columns: 1fr 80px 90px; gap: 0.5rem; align-items: center; padding: 0.5rem 0.85rem; border-bottom: 1px solid var(--border); }
  .row:hover { background: var(--surface); }
  .row .name { font-weight: 500; }
  .row .meta { font-size: 0.78rem; color: var(--muted); margin-top: 0.1rem; }
  .row.saved { opacity: 0.45; }
  .row.saved .saved-badge { color: #22c55e; font-size: 0.85rem; }
  input[type="text"] { background: var(--surface2); border: 1px solid var(--border); color: var(--fg); padding: 0.4rem 0.55rem; border-radius: 6px; font-family: inherit; font-size: 0.9rem; width: 100%; text-transform: uppercase; }
  input[type="text"]:focus { outline: 2px solid var(--accent); }
  button { background: var(--accent); color: #0b0814; border: 0; padding: 0.4rem 0.65rem; border-radius: 6px; font-family: inherit; font-size: 0.85rem; font-weight: 600; cursor: pointer; }
  button:hover:not(:disabled) { filter: brightness(1.1); }
  button:disabled { opacity: 0.45; cursor: default; }
  .toolbar { margin: 1.5rem 0; display: flex; gap: 0.6rem; }
  .toolbar a, .toolbar button { background: var(--surface2); border: 1px solid var(--border); color: var(--fg); padding: 0.55rem 1rem; border-radius: 999px; text-decoration: none; font-size: 0.9rem; cursor: pointer; }
  .toolbar a:hover, .toolbar button:hover { background: rgba(167,139,250,0.18); border-color: var(--accent); color: var(--accent); }
  .empty { text-align: center; padding: 3rem; color: var(--muted); }
  .legend { display: grid; grid-template-columns: 1fr 80px 90px; gap: 0.5rem; padding: 0 0.85rem 0.5rem; color: var(--muted); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.08em; }
</style>
</head>
<body>
<main>
  <h1>Assign countries</h1>
  <p class="sub">Pick the ISO 2-letter code (US, FR, GB, JP, …) for each new artist. Saves immediately to the local DB. Click "Sync &amp; publish" when you're done to push to GitHub.</p>

  <div id="list">Loading…</div>

  <div class="toolbar">
    <button id="refresh-btn">Sync &amp; publish</button>
    <a href="https://benjaminbellman.github.io/music-dashboard/">← Back to dashboard</a>
  </div>
</main>

<script>
const list = document.getElementById("list");

async function load() {
  const r = await fetch("/pending");
  const pending = await r.json();
  if (!pending.length) {
    list.innerHTML = '<div class="empty">🎉 Nothing pending. Every artist has a country.</div>';
    return;
  }
  list.innerHTML = '<div class="legend"><div>Artist</div><div>Country</div><div></div></div>' +
    pending.map(p => `
      <div class="row" data-artist="${escapeHTML(p.artist)}">
        <div>
          <div class="name">${escapeHTML(p.artist)}</div>
          <div class="meta">${p.tracks || 0} track${p.tracks === 1 ? "" : "s"} · ${(p.plays || 0).toLocaleString()} plays</div>
        </div>
        <input type="text" maxlength="2" placeholder="FR" autocapitalize="characters" />
        <button>Save</button>
      </div>
    `).join("");

  list.querySelectorAll(".row").forEach(row => {
    const input = row.querySelector("input");
    const btn = row.querySelector("button");
    const save = async () => {
      const v = input.value.trim().toUpperCase();
      if (!/^[A-Z]{2}$/.test(v)) { input.focus(); return; }
      btn.disabled = true; btn.textContent = "Saving…";
      const r = await fetch("/assign", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({ artist: row.dataset.artist, country: v }),
      });
      if (r.ok) {
        row.classList.add("saved");
        btn.replaceWith(Object.assign(document.createElement("span"), { className: "saved-badge", textContent: "✓ Saved" }));
      } else {
        btn.disabled = false; btn.textContent = "Save"; alert("Save failed.");
      }
    };
    btn.addEventListener("click", save);
    input.addEventListener("keydown", e => e.key === "Enter" && save());
  });
}

document.getElementById("refresh-btn").addEventListener("click", () => {
  location.href = "/";
});

function escapeHTML(s) { return String(s ?? "").replace(/[&<>\"']/g, c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c])); }

load();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def _write_cors(self) -> None:
        origin = self.headers.get("Origin", "")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Vary", "Origin")

    def _send_json(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self._write_cors()
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._write_cors()
        self.end_headers()

    def do_GET(self) -> None:
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        if path == "/ping":
            self._send_json(200, {"ok": True})
        elif path in ("", "/refresh"):
            self._send_html(STATUS_PAGE)
        elif path == "/assign":
            self._send_html(ASSIGN_PAGE)
        elif path == "/pending":
            try:
                self._send_json(200, _pending_artists())
            except Exception as e:  # noqa: BLE001
                self._send_json(500, {"error": str(e)})
        elif path == "/ask":
            question = (parse_qs(parsed.query).get("q", [""])[0]).strip()
            if not question:
                self._send_html(ask_page("", "Type a question below to get started.", ""))
                return
            result = _ask_claude(question)
            if "error" in result:
                html = f'<span>{result["error"]}</span>'
                self._send_html(ask_page(question, html).replace('class="answer"', 'class="answer err"'))
            else:
                # Simple safe rendering — Claude returns plain text; preserve newlines.
                text = (result["answer"]
                        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
                meta = (
                    f"{result['model']} · {result['tokens_in']} in / {result['tokens_out']} out"
                    f" · this call ≈ ${result['cost']:.4f}"
                    f" · today ${result['spent_today']:.4f} / ${result['cap_usd']:.2f}"
                    f" ({result['asked_today']} questions)"
                )
                self._send_html(ask_page(question, text, meta))
        else:
            self._send_json(404, {"error": "not found"})

    def _send_html(self, html: str) -> None:
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._write_cors()
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        if self.path.rstrip("/") == "/assign":
            length = int(self.headers.get("Content-Length") or 0)
            body_raw = self.rfile.read(length).decode("utf-8")
            params = parse_qs(body_raw)
            artist = (params.get("artist", [""])[0]).strip()
            country = (params.get("country", [""])[0]).strip()
            if not artist or not country:
                self._send_json(400, {"error": "artist and country required"})
                return
            try:
                _save_country(artist, country)
                self._send_json(200, {"ok": True, "artist": artist, "country": country.upper()})
            except Exception as e:  # noqa: BLE001
                self._send_json(400, {"error": str(e)})
            return

        if self.path.rstrip("/") not in ("/refresh", "/sync"):
            self._send_json(404, {"error": "not found"})
            return
        if not _lock.acquire(blocking=False):
            self._send_json(409, {"ok": False, "error": "a sync is already running"})
            return
        try:
            result = subprocess.run(
                ["/bin/bash", str(SYNC_SCRIPT)],
                capture_output=True,
                text=True,
                timeout=600,
            )
            self._send_json(
                200 if result.returncode == 0 else 500,
                {
                    "ok": result.returncode == 0,
                    "returncode": result.returncode,
                    "stdout": result.stdout[-4000:],
                    "stderr": result.stderr[-4000:],
                },
            )
        except subprocess.TimeoutExpired:
            self._send_json(504, {"ok": False, "error": "sync timed out after 10 min"})
        except Exception as e:  # noqa: BLE001
            self._send_json(500, {"ok": False, "error": str(e)})
        finally:
            _lock.release()

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        # Silence default stderr access log; launchd logs capture anything useful.
        pass


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"refresh-server: listening on http://127.0.0.1:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
