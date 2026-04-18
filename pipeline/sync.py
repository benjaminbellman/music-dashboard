#!/usr/bin/env python3
"""Extract the macOS Music library via AppleScript and upsert into SQLite.

Writes two tables in data/music.db:
  - tracks_current: full replacement of the latest snapshot
  - snapshots:       append-only history keyed by (date, track_id)

Also populates artist_country_pending for any artist not yet in the country ledger.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import subprocess
import sys
from pathlib import Path

from db import connect, now_iso, today_iso

PROJECT_DIR = Path(__file__).resolve().parent
APPLESCRIPT = PROJECT_DIR / "extract_library.applescript"

FS = "\x1f"
RS = "\x1e"


def run_applescript() -> str:
    result = subprocess.run(
        ["osascript", str(APPLESCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise SystemExit(
            f"osascript failed (exit {result.returncode}). "
            "If this is the first run, macOS may need to prompt for Automation "
            "permission to control Music."
        )
    return result.stdout


def track_id(song: str, artist: str, album: str) -> str:
    key = f"{song}\x00{artist}\x00{album}".lower()
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def parse_duration(seconds_raw: str) -> int | None:
    if not seconds_raw:
        return None
    try:
        return int(round(float(seconds_raw)))
    except ValueError:
        return None


def parse_plays(plays_raw: str) -> int:
    if not plays_raw:
        return 0
    try:
        return int(plays_raw)
    except ValueError:
        return 0


def parse_iso(raw: str) -> str | None:
    if not raw:
        return None
    try:
        return dt.datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S").isoformat()
    except ValueError:
        return None


def parse_rows(raw: str) -> list[tuple]:
    rows: list[tuple] = []
    for record in raw.split(RS):
        record = record.strip("\n\r")
        if not record:
            continue
        fields = record.split(FS)
        if len(fields) != 8:
            continue
        song, dur, artist, album, plays, added, played, genre = [f.strip() for f in fields]
        rows.append(
            (
                track_id(song, artist, album),
                song,
                artist,
                album,
                parse_duration(dur),
                parse_plays(plays),
                parse_iso(added),
                parse_iso(played),
                genre,
            )
        )
    return rows


def _dedupe(rows: list[tuple]) -> list[tuple]:
    """Merge rows sharing the same track_id by summing plays and keeping latest dates."""
    merged: dict[str, list] = {}
    for r in rows:
        tid, song, artist, album, dur, plays, added, played, genre = r
        existing = merged.get(tid)
        if not existing:
            merged[tid] = list(r)
            continue
        # Sum plays
        existing[5] = (existing[5] or 0) + (plays or 0)
        # Earliest date_added (keeping original), latest last_played
        if added and (not existing[6] or added < existing[6]):
            existing[6] = added
        if played and (not existing[7] or played > existing[7]):
            existing[7] = played
        # Prefer non-null duration / genre from either row
        existing[4] = existing[4] or dur
        existing[8] = existing[8] or genre
    return [tuple(v) for v in merged.values()]


def upsert_tracks(conn, rows: list[tuple]) -> None:
    with conn:
        conn.execute("DELETE FROM tracks_current")
        conn.executemany(
            """
            INSERT INTO tracks_current
              (track_id, song, artist, album, duration_sec, plays, date_added, last_played, genre)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )


def append_snapshot(conn, rows: list[tuple]) -> None:
    date = today_iso()
    with conn:
        # If we already synced today, replace today's snapshot entries.
        conn.execute("DELETE FROM snapshots WHERE snapshot_date = ?", (date,))
        conn.executemany(
            "INSERT INTO snapshots (snapshot_date, track_id, plays, last_played) VALUES (?,?,?,?)",
            [(date, r[0], r[5], r[7]) for r in rows],
        )


def queue_new_artists(conn) -> int:
    """Put any artist we've never seen in the country ledger into the pending queue."""
    ts = now_iso()
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO artist_country_pending (artist, first_seen)
        SELECT DISTINCT t.artist, ?
        FROM tracks_current t
        LEFT JOIN artist_country ac ON ac.artist = t.artist
        LEFT JOIN artist_country_pending p ON p.artist = t.artist
        WHERE ac.artist IS NULL AND p.artist IS NULL AND t.artist <> ''
        """,
        (ts,),
    )
    conn.commit()
    return cursor.rowcount or 0


def main() -> None:
    print(f"[{now_iso()}] sync: extracting Music library via AppleScript")
    raw = run_applescript()
    rows = parse_rows(raw)
    if not rows:
        raise SystemExit("No rows parsed from AppleScript output; aborting.")
    rows = _dedupe(rows)

    conn = connect()
    upsert_tracks(conn, rows)
    append_snapshot(conn, rows)
    new_pending = queue_new_artists(conn)

    total_plays = conn.execute("SELECT COALESCE(SUM(plays), 0) FROM tracks_current").fetchone()[0]
    artist_count = conn.execute(
        "SELECT COUNT(DISTINCT artist) FROM tracks_current WHERE artist <> ''"
    ).fetchone()[0]

    print(
        f"sync: {len(rows):,} tracks  |  {artist_count:,} artists  |  "
        f"{total_plays:,} total plays  |  {new_pending} new artists queued for enrichment"
    )


if __name__ == "__main__":
    main()
