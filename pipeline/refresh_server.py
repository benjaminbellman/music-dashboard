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
}

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
        if self.path.rstrip("/") == "/ping":
            self._send_json(200, {"ok": True})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/refresh":
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
