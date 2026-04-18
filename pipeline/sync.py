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
import re
import subprocess
import sys
from pathlib import Path

from db import connect, now_iso, today_iso

# Split an artist field on common collaboration delimiters and keep the first
# token as the "primary artist". Lets us roll up "Bon Entendeur",
# "Bon Entendeur & X", "Bon Entendeur feat. Y" into one bucket — matching the
# spirit of the legacy xlsm's Format_Artist_Plays SUMIF wildcard logic.
#
# Known limitation: bands with "&" in their name (Hall & Oates, Earth Wind &
# Fire) will split incorrectly. Caller can mark these as canonical via the
# artist_country ledger if needed.
_SPLIT_COLLAB = re.compile(
    r"\s+(?:feat\.?|ft\.?|with|vs\.?|and|/|x)\s+",
    re.IGNORECASE,
)
_SPLIT_AMP = re.compile(r"\s*&\s*")


def primary_artist(artist: str, canonical: set[str] | None = None) -> str:
    """Roll an artist string up to its lead artist for grouping.

    Two-pass split:
      1. Always strip unambiguous collaboration markers (feat., ft., with, vs,
         and, /, x). These never appear in real band names.
      2. Try splitting on `&`, but ONLY accept the split if the resulting
         head is a known canonical artist. This preserves band names whose
         names contain `&` (Polo & Pan, Hall & Oates, Earth Wind & Fire) while
         still splitting genuine collaborations (Bon Entendeur & Pierre Niney).
    """
    if not artist:
        return ""
    raw = artist.strip()
    canonical = canonical or set()

    # Pass 1: peel off feat./ft./and/with/vs/x/ slash collaborators.
    head = _SPLIT_COLLAB.split(raw, maxsplit=1)[0].strip()

    # Pass 2: try `&` split, accept only if the head is canonical.
    amp_head = _SPLIT_AMP.split(head, maxsplit=1)[0].strip()
    if amp_head != head and amp_head in canonical:
        return amp_head

    return head


_SPLIT_ALL = re.compile(
    r"\s+(?:&|feat\.?|ft\.?|with|vs\.?|and|/|x)\s+",
    re.IGNORECASE,
)


def credited_artists(artist: str, canonical: set[str] | None = None) -> list[str]:
    """List every artist that should be credited for a song.

    The lead (primary_artist) is always credited. Other artists in the raw
    string are credited only if they're in the canonical set — this avoids
    creating phantom artists for ad-hoc featurings while still giving credit
    when a known artist (The Weeknd, Daft Punk, Pharrell Williams) appears as
    a feature on someone else's track.

    If the FULL string is canonical (band like 'Polo & Pan'), it's the only
    credit — we don't split into 'Polo' + 'Pan'.
    """
    if not artist:
        return []
    raw = artist.strip()
    canonical = canonical or set()
    primary = primary_artist(raw, canonical)

    # Whole string is a canonical band name → credit it as one.
    if primary == raw:
        return [raw]

    credits = [primary]
    for atom in _SPLIT_ALL.split(raw):
        a = atom.strip()
        if a and a != primary and a in canonical and a not in credits:
            credits.append(a)
    return credits

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


def parse_rows(raw: str, canonical: set[str] | None = None) -> list[tuple]:
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
                primary_artist(artist, canonical),
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
    """Merge rows sharing the same track_id by summing plays and keeping latest dates.
    Tuple shape: (tid, song, artist, primary_artist, album, dur, plays, added, played, genre)
    """
    merged: dict[str, list] = {}
    for r in rows:
        tid = r[0]
        existing = merged.get(tid)
        if not existing:
            merged[tid] = list(r)
            continue
        # Sum plays (index 6)
        existing[6] = (existing[6] or 0) + (r[6] or 0)
        # Earliest date_added (index 7), latest last_played (index 8)
        if r[7] and (not existing[7] or r[7] < existing[7]):
            existing[7] = r[7]
        if r[8] and (not existing[8] or r[8] > existing[8]):
            existing[8] = r[8]
        # Prefer non-null duration / genre
        existing[5] = existing[5] or r[5]
        existing[9] = existing[9] or r[9]
    return [tuple(v) for v in merged.values()]


def upsert_tracks(conn, rows: list[tuple], canonical: set[str]) -> None:
    with conn:
        conn.execute("DELETE FROM tracks_current")
        conn.executemany(
            """
            INSERT INTO tracks_current
              (track_id, song, artist, primary_artist, album, duration_sec,
               plays, date_added, last_played, genre)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )
        # Rebuild the multi-artist credit table.
        conn.execute("DELETE FROM track_artists")
        credits = []
        for r in rows:
            tid, _song, raw_artist, _primary, *_rest = r
            for a in credited_artists(raw_artist, canonical):
                credits.append((tid, a))
        conn.executemany(
            "INSERT OR IGNORE INTO track_artists (track_id, artist) VALUES (?, ?)",
            credits,
        )


def append_snapshot(conn, rows: list[tuple]) -> None:
    date = today_iso()
    with conn:
        # If we already synced today, replace today's snapshot entries.
        conn.execute("DELETE FROM snapshots WHERE snapshot_date = ?", (date,))
        conn.executemany(
            "INSERT INTO snapshots (snapshot_date, track_id, plays, last_played) VALUES (?,?,?,?)",
            [(date, r[0], r[6], r[8]) for r in rows],
        )


def queue_new_artists(conn) -> int:
    """Put any primary_artist we've never seen in the country ledger into the
    pending queue. We key on primary_artist so collaborations roll up to the
    lead artist instead of queueing every "X & Y" variant separately. Also
    clears any stale pending rows that have since been resolved.
    """
    ts = now_iso()
    with conn:
        # Clean stale: anything pending that's now in the ledger.
        conn.execute(
            "DELETE FROM artist_country_pending "
            "WHERE artist IN (SELECT artist FROM artist_country)"
        )
        # Clean stale: anything pending that no longer appears in tracks_current
        # (artist was renamed / song deleted / no longer in library).
        conn.execute(
            "DELETE FROM artist_country_pending "
            "WHERE artist NOT IN (SELECT DISTINCT primary_artist FROM tracks_current)"
        )
        # Add any new ones.
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO artist_country_pending (artist, first_seen)
            SELECT DISTINCT t.primary_artist, ?
            FROM tracks_current t
            LEFT JOIN artist_country ac ON ac.artist = t.primary_artist
            LEFT JOIN artist_country_pending p ON p.artist = t.primary_artist
            WHERE ac.artist IS NULL AND p.artist IS NULL AND t.primary_artist <> ''
            """,
            (ts,),
        )
    return cursor.rowcount or 0


def main() -> None:
    print(f"[{now_iso()}] sync: extracting Music library via AppleScript")
    raw = run_applescript()

    # Load the canonical artist set so primary_artist() doesn't split duos
    # already in the country ledger (Polo & Pan, Hall & Oates, …).
    conn = connect()
    canonical = {
        r[0] for r in conn.execute("SELECT artist FROM artist_country")
    }

    rows = parse_rows(raw, canonical)
    if not rows:
        raise SystemExit("No rows parsed from AppleScript output; aborting.")
    rows = _dedupe(rows)

    upsert_tracks(conn, rows, canonical)
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
