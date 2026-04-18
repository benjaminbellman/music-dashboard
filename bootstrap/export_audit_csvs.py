#!/usr/bin/env python3
"""Dump audit CSVs so you can sanity-check the numbers feeding the dashboard.

Writes four files into ./audit_export/:
  - 1_tracks_current.csv       every row in tracks_current
  - 2_artist_mapping.csv       raw artist → primary_artist (one per distinct raw)
  - 3_primary_artist_totals.csv  primary_artist totals (matches dashboard top-artists)
  - 4_year_breakdown.csv       per-(year, primary_artist) plays — rows used by
                               the 'Artist of the year' tiles
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "music.db"
OUT = ROOT / "audit_export"
OUT.mkdir(exist_ok=True)


def write(path: Path, rows, header):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print(f"  wrote {path.relative_to(ROOT)}  ({path.stat().st_size:,} bytes)")


def main() -> None:
    c = sqlite3.connect(str(DB))
    c.row_factory = sqlite3.Row

    print("export-audit:")

    # 1. Every track
    rows = list(c.execute("""
        SELECT t.song, t.artist, t.primary_artist, ac.country,
               t.album, t.duration_sec, t.plays,
               t.date_added, t.last_played, t.genre,
               substr(t.last_played, 1, 4) AS last_played_year
        FROM tracks_current t
        LEFT JOIN artist_country ac ON ac.artist = t.primary_artist
        ORDER BY t.plays DESC, t.artist
    """))
    write(
        OUT / "1_tracks_current.csv",
        [tuple(r) for r in rows],
        ["song", "artist_raw", "primary_artist", "country",
         "album", "duration_sec", "plays",
         "date_added", "last_played", "genre", "last_played_year"],
    )

    # 2. Artist mapping
    rows = list(c.execute("""
        SELECT artist AS artist_raw,
               primary_artist,
               COUNT(*) AS songs,
               SUM(plays) AS plays
        FROM tracks_current
        GROUP BY artist, primary_artist
        ORDER BY primary_artist, -SUM(plays)
    """))
    write(
        OUT / "2_artist_mapping.csv",
        [tuple(r) for r in rows],
        ["artist_raw", "primary_artist", "songs", "plays"],
    )

    # 3. Primary artist totals (this matches dashboard top-artists)
    rows = list(c.execute("""
        SELECT t.primary_artist,
               ac.country,
               COUNT(*) AS songs,
               SUM(t.plays) AS plays
        FROM tracks_current t
        LEFT JOIN artist_country ac ON ac.artist = t.primary_artist
        WHERE t.primary_artist <> ''
        GROUP BY t.primary_artist
        ORDER BY -SUM(t.plays)
    """))
    write(
        OUT / "3_primary_artist_totals.csv",
        [tuple(r) for r in rows],
        ["primary_artist", "country", "songs", "plays"],
    )

    # 4. Year × primary_artist (the rows that decide 'Artist of the year').
    # Year = year the song was added to the library; plays = lifetime plays
    # of those songs.
    rows = list(c.execute("""
        SELECT substr(t.date_added, 1, 4) AS year_added,
               t.primary_artist,
               COUNT(*) AS songs,
               SUM(t.plays) AS plays
        FROM tracks_current t
        WHERE t.date_added IS NOT NULL AND t.primary_artist <> ''
        GROUP BY year_added, t.primary_artist
        ORDER BY year_added DESC, -SUM(t.plays)
    """))
    write(
        OUT / "4_year_breakdown.csv",
        [tuple(r) for r in rows],
        ["year_added", "primary_artist", "songs", "plays"],
    )

    print(f"\nopen the folder: open {OUT}")


if __name__ == "__main__":
    main()
