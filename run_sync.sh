#!/usr/bin/env bash
# Weekly (or on-demand) sync: extract Music library → enrich → rebuild aggregates
# → commit + push. Git push triggers GitHub Actions to rebuild and deploy the
# dashboard to GitHub Pages.

set -euo pipefail

PROJECT_DIR="/Users/benjaminbellman/Music Dashboard"
cd "$PROJECT_DIR"

export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"

LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/sync.log"

echo "────────────────────────────────────────" >> "$LOG"
date -u +"[%Y-%m-%dT%H:%M:%SZ] run_sync starting" >> "$LOG"

VENV_PY="$PROJECT_DIR/.venv/bin/python"

"$VENV_PY" pipeline/sync.py        >> "$LOG" 2>&1
"$VENV_PY" pipeline/enrich.py      >> "$LOG" 2>&1 || echo "enrich failed (non-fatal)" >> "$LOG"
"$VENV_PY" pipeline/build_data.py  >> "$LOG" 2>&1

if [[ -n "$(git status --porcelain data/ dashboard/data/ 2>/dev/null || true)" ]]; then
    git add data/ dashboard/data/
    git -c user.email="sync@localhost" \
        -c user.name="music-dashboard sync" \
        commit -m "sync: $(date -u +%Y-%m-%dT%H:%MZ)" >> "$LOG" 2>&1
    git push >> "$LOG" 2>&1 || echo "push failed (not fatal; will retry next run)" >> "$LOG"
else
    echo "no data changes" >> "$LOG"
fi

date -u +"[%Y-%m-%dT%H:%M:%SZ] run_sync finished" >> "$LOG"
