#!/usr/bin/env python3
"""Tiny localhost HTTP server that lets the dashboard's Refresh button trigger
a full sync from the browser. Runs on 127.0.0.1:8789, bound to loopback only.

Endpoints:
  GET  /ping      → {ok: true}          (dashboard uses this to detect availability)
  POST /refresh   → runs run_sync.sh, returns {ok, stdout, stderr}

CORS is restricted to the deployed dashboard origin plus local previews.
"""

from __future__ import annotations

import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
SYNC_SCRIPT = PROJECT_DIR / "run_sync.sh"
PORT = 8789

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
        path = self.path.rstrip("/")
        if path == "/ping":
            self._send_json(200, {"ok": True})
        elif path in ("", "/refresh"):
            self._send_html(STATUS_PAGE)
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
