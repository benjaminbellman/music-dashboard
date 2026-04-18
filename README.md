# Music Dashboard

Live, Mac-native replacement for the old `Itunes_Dashboard_26.xlsm` VBA workbook.

Your Mac extracts the Apple Music library weekly → enriches any new artist with a country via MusicBrainz → rebuilds JSON aggregates → commits to git. GitHub Actions deploys the static dashboard to GitHub Pages on every push.

The dashboard is a single static page (no build step) — it loads [Observable Plot](https://observablehq.com/plot/) and D3 from a CDN at runtime and fetches `dashboard/data/aggregates.json`.

## Flow

```
  launchd (weekly)                        GitHub Actions
        │                                       │
        ▼                                       ▼
  run_sync.sh                           upload dashboard/ → Pages
  ├─ sync.py      (AppleScript → SQLite)
  ├─ enrich.py    (MusicBrainz + manual fallback)
  ├─ build_data.py (SQLite → data/aggregates/*.json
  │                       → dashboard/data/aggregates.json)
  └─ git push ─────────────────────────────────→ GitHub
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

dashboard/                         static site (deployed to GitHub Pages)
  index.html                       shell with tabs
  app.js                           fetches aggregates, renders charts
  style.css                        dark purple theme
  data/aggregates.json             bundled aggregates the site reads

run_sync.sh                        launchd entry point
com.benjamin.musicdashboardsync.plist   # copy to ~/Library/LaunchAgents/
.github/workflows/deploy.yml       # Pages deploy
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
cd dashboard && python3 -m http.server 8788     # open http://localhost:8788
```

## Day-to-day

```sh
bash run_sync.sh                               # on-demand refresh: extract → enrich → build → commit → push
.venv/bin/python pipeline/enrich.py --interactive   # assign countries MusicBrainz couldn't resolve
```

## GitHub Pages deploy

1. Create the repo on GitHub (`music-dashboard`) and push main.
2. **Repo Settings → Pages → Source = "GitHub Actions"**.
3. Every push that touches `dashboard/**` runs `.github/workflows/deploy.yml`.

## Scheduling the weekly sync

```sh
cp com.benjamin.musicdashboardsync.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.benjamin.musicdashboardsync.plist
launchctl start com.benjamin.musicdashboardsync   # run immediately to test
tail -f logs/sync.log
```

Cadence: Mondays 9:00 local.

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
