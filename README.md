# Music Dashboard

**Live site: https://benjaminbellman.github.io/music-dashboard/**

Mac-native replacement for the old `Itunes_Dashboard_26.xlsm` VBA workbook.

Your Mac extracts the Apple Music library weekly → enriches any new artist with a country via MusicBrainz → rebuilds JSON aggregates → commits to git. GitHub Pages serves `docs/` directly on every push.

The dashboard is a single static page (no build step) — it loads [Observable Plot](https://observablehq.com/plot/) and D3 from a CDN at runtime and fetches `docs/data/aggregates.json`.

## Flow

```
  launchd (weekly)                         GitHub
        │                                     │
        ▼                                     ▼
  run_sync.sh                           Pages (branch-based)
  ├─ sync.py      (AppleScript → SQLite)      serves /docs on push
  ├─ enrich.py    (MusicBrainz + manual fallback)
  ├─ build_data.py (SQLite → data/aggregates/*.json
  │                       → docs/data/aggregates.json)
  └─ git push ─────────────────────────→ github.com/benjaminbellman/music-dashboard
```

## Layout

```
pipeline/                          data pipeline (Python)
  extract_library.applescript
  sync.py                          library → tracks_current + snapshots
  db.py                            SQLite schema + connection
  enrich.py                        MusicBrainz + manual fallback
  build_data.py                    SQLite → JSON aggregates

bootstrap/
  seed_country_ledger.py           one-off: xlsm → artist_country_seed.csv

data/
  music.db                         canonical SQLite (committed)
  artist_country_seed.csv
  aggregates/*.json                one file per dashboard section (canonical)

docs/                         static site (deployed to GitHub Pages)
  index.html                       shell with tabs
  app.js                           fetches aggregates, renders charts
  style.css                        dark purple theme
  data/aggregates.json             bundled aggregates the site reads

run_sync.sh                        launchd entry point
com.benjamin.musicdashboardsync.plist   # copy to ~/Library/LaunchAgents/
```

## One-time setup

```sh
cd "/Users/benjaminbellman/Music Dashboard"

# Python venv
python3 -m venv .venv
.venv/bin/pip install musicbrainzngs openpyxl

# Bootstrap country ledger from the legacy xlsm
.venv/bin/python bootstrap/seed_country_ledger.py
.venv/bin/python pipeline/enrich.py --import-csv data/artist_country_seed.csv

# First sync (macOS may prompt for Automation permission to control Music)
.venv/bin/python pipeline/sync.py
.venv/bin/python pipeline/enrich.py        # auto-resolve new artists via MusicBrainz
.venv/bin/python pipeline/build_data.py

# Preview the dashboard locally
cd docs && python3 -m http.server 8788          # open http://localhost:8788
```

## Day-to-day

```sh
bash run_sync.sh                                    # on-demand refresh: extract → enrich → build → commit → push
.venv/bin/python pipeline/enrich.py --interactive   # assign countries MusicBrainz couldn't resolve
```

### Ask Claude about your library (optional)

The Insights tab has an "Ask Claude" box. It hits the refresh-server's
`/ask` endpoint, which calls the Anthropic API with your aggregates as
context. To enable:

```sh
mkdir -p credentials
echo "sk-ant-your-key-here" > credentials/anthropic.key
launchctl unload ~/Library/LaunchAgents/com.benjamin.musicdashboardrefresh.plist
launchctl load   ~/Library/LaunchAgents/com.benjamin.musicdashboardrefresh.plist
```

`credentials/` is gitignored so the key never gets pushed. The dashboard
works fine without it — the Ask card just shows the setup hint.

There's also a **Refresh** button in the dashboard topbar. It appears only when
your browser can reach the local refresh server
(`pipeline/refresh_server.py`, loopback on `127.0.0.1:8789`). Clicking it triggers
the same `run_sync.sh` flow, then reloads the page once GitHub Pages redeploys.
When you view the dashboard from another device (phone, another laptop), the
button stays hidden.

Install the refresh-server launchd job once:

```sh
cp com.benjamin.musicdashboardrefresh.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.benjamin.musicdashboardrefresh.plist
```

## GitHub Pages deploy

1. Create the repo on GitHub (`music-dashboard`) and push main.
2. **Repo Settings → Pages → Source = "Deploy from a branch"**
3. **Branch = `main`**, **Folder = `/docs`** → Save.
4. Every push to `main` re-deploys within ~1 minute.

## Scheduling the weekly sync

```sh
cp com.benjamin.musicdashboardsync.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.benjamin.musicdashboardsync.plist
launchctl start com.benjamin.musicdashboardsync   # run immediately to test
tail -f logs/sync.log
```

Cadence: every day at 9:00 local. Daily snapshots feed the "Recent listening trends" charts on the Insights tab — coarser cadences would only give weekly resolution.

## Data model (SQLite)

- `tracks_current` — one row per unique `(song, artist, album)` from the latest sync
- `snapshots` — append-only, `(snapshot_date, track_id)` — for plays-over-time charts
- `artist_country` — country ledger: `(artist, country, source, updated)` with `source ∈ {seed, musicbrainz, manual}`
- `artist_country_pending` — artists MusicBrainz couldn't resolve; surfaced on the Pending tab

## Troubleshooting

- **`osascript failed` on first run**: macOS didn't prompt for Automation permission. Run `python pipeline/sync.py` from Terminal once, accept the OS prompt.
- **MusicBrainz rate-limited**: `enrich.py` sleeps 1.05s between calls per their policy (<https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting>).
- **Launchd can't talk to Music**: launchd runs under a different TCC context than Terminal. Grant Automation permission to `/bin/bash` in **System Settings → Privacy & Security → Automation**.
- **Dashboard shows old data**: make sure `run_sync.sh` committed and pushed. GitHub Actions deploys within ~2 minutes.

### Refresh button broken / sync silently fails after a macOS update

**Symptoms** (any of these):
- Clicking Refresh in the dashboard does nothing, or browser says "can't connect to 127.0.0.1:8789".
- `curl http://127.0.0.1:8789/ping` times out / refuses connection.
- `launchctl list | grep musicdashboardrefresh` shows a non-zero exit status (commonly `78`) and a `-` in the PID column.
- Running anything in `pipeline/` directly fails with `No such file or directory: .venv/bin/python`.
- `ls -la /Library/Developer/CommandLineTools/usr/bin/python3` reports the file doesn't exist.

**Root cause:** the macOS Command Line Tools (or a full macOS upgrade) replaced `/Library/Developer/CommandLineTools/usr/bin/python3`. The project's `.venv/bin/python` is a symlink chain that ultimately pointed at that file → broken venv → every Python entry point in the project fails → launchd can't start the refresh-server, and `run_sync.sh` would silently exit if it ever ran.

**Fix recipe** (re-runnable, takes ~30s):

```sh
cd "/Users/benjaminbellman/Music Dashboard"

# 1. Rebuild the venv against the macOS-shipped python (more update-stable than CLT's).
rm -rf .venv
/usr/bin/python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install musicbrainzngs openpyxl anthropic

# 2. Sanity check.
.venv/bin/python -c "import anthropic, musicbrainzngs, openpyxl; print('OK')"

# 3. Restart the always-on refresh server.
launchctl unload ~/Library/LaunchAgents/com.benjamin.musicdashboardrefresh.plist
launchctl load   ~/Library/LaunchAgents/com.benjamin.musicdashboardrefresh.plist

# 4. Verify.
sleep 2 && curl -s http://127.0.0.1:8789/ping     # → {"ok": true}
launchctl list | grep musicdashboardrefresh       # → <PID>  0  com.benjamin.musicdashboardrefresh
```

**Why `/usr/bin/python3` and not Homebrew/CLT/pyenv:** `/usr/bin/python3` ships with macOS itself and survives both Xcode/CLT updates and most macOS point updates. It's the most stable target for a long-lived launchd job. If a major macOS upgrade ever does break it too, swap in whichever python3 is freshest on the system — the dependency list is small and the venv rebuild is cheap.

**Note for whoever (Claude or otherwise) is looking at this in the future:** if the user reports "refresh button broke" or "the dashboard isn't updating," check `launchctl list | grep musicdashboard` and `ls -la /Library/Developer/CommandLineTools/usr/bin/python3` *first*. A broken CLT python is by far the most common single cause of total pipeline failure on this project. The fix above is idempotent and safe to re-run.
