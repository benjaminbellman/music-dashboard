#!/usr/bin/env python3
"""Read data/music.db and emit pre-computed JSON aggregates under data/aggregates/.

Each JSON is a 1:1 map to a dashboard view, so we can verify values against
the legacy Excel dashboard on a per-view basis.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from db import connect

ROOT = Path(__file__).resolve().parent.parent
AGGREGATES_DIR = ROOT / "data" / "aggregates"
DASHBOARD_DATA = ROOT / "dashboard" / "data"

_emitted: dict = {}


def _write(name: str, obj) -> None:
    """Write each aggregate to data/aggregates/<name>.json AND accumulate into
    the combined file the dashboard loads."""
    path = AGGREGATES_DIR / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2))
    _emitted[name] = obj
    print(f"  wrote {path.relative_to(ROOT)}")


def _write_combined() -> None:
    DASHBOARD_DATA.mkdir(parents=True, exist_ok=True)
    combined = DASHBOARD_DATA / "aggregates.json"
    combined.write_text(json.dumps(_emitted, ensure_ascii=False))
    print(f"  wrote {combined.relative_to(ROOT)} (combined, {sum(len(str(v)) for v in _emitted.values()):,} chars)")


# ---------- Aggregates ----------

def kpis(conn) -> dict:
    row = conn.execute(
        """
        SELECT
            COALESCE(SUM(plays), 0) AS total_plays,
            COUNT(*)                AS track_count,
            COUNT(DISTINCT CASE WHEN artist <> '' THEN artist END) AS artist_count
        FROM tracks_current
        """
    ).fetchone()
    plays = [r[0] for r in conn.execute("SELECT plays FROM tracks_current")]
    avg = statistics.mean(plays) if plays else 0
    median = statistics.median(plays) if plays else 0
    stdev = statistics.pstdev(plays) if plays else 0
    return {
        "total_plays": row["total_plays"],
        "track_count": row["track_count"],
        "artist_count": row["artist_count"],
        "avg_plays": round(avg, 4),
        "median_plays": median,
        "stdev_plays": round(stdev, 4),
    }


def top_artists(conn) -> dict:
    by_song = [
        {"rank": i + 1, "artist": r["artist"], "count": r["c"], "country": r["country"]}
        for i, r in enumerate(conn.execute(
            """
            SELECT t.artist, COUNT(*) AS c, ac.country
            FROM tracks_current t
            LEFT JOIN artist_country ac ON ac.artist = t.artist
            WHERE t.artist <> ''
            GROUP BY t.artist
            ORDER BY c DESC, t.artist
            LIMIT 20
            """
        ))
    ]
    by_plays = [
        {"rank": i + 1, "artist": r["artist"], "plays": r["p"], "country": r["country"]}
        for i, r in enumerate(conn.execute(
            """
            SELECT t.artist, SUM(t.plays) AS p, ac.country
            FROM tracks_current t
            LEFT JOIN artist_country ac ON ac.artist = t.artist
            WHERE t.artist <> ''
            GROUP BY t.artist
            ORDER BY p DESC, t.artist
            LIMIT 20
            """
        ))
    ]
    return {"by_song_count": by_song, "by_play_count": by_plays}


def country_plays(conn) -> list:
    return [
        {
            "country": r["country"],
            "plays": r["plays"],
            "artists": r["artists"],
            "songs": r["songs"],
        }
        for r in conn.execute(
            """
            SELECT ac.country,
                   SUM(t.plays)                  AS plays,
                   COUNT(DISTINCT t.artist)      AS artists,
                   COUNT(*)                      AS songs
            FROM tracks_current t
            JOIN artist_country ac ON ac.artist = t.artist
            GROUP BY ac.country
            ORDER BY plays DESC
            """
        )
    ]


def genre_plays(conn) -> list:
    return [
        {"genre": r["genre"], "plays": r["plays"], "songs": r["songs"]}
        for r in conn.execute(
            """
            SELECT COALESCE(NULLIF(genre, ''), 'Unspecified') AS genre,
                   SUM(plays) AS plays,
                   COUNT(*)   AS songs
            FROM tracks_current
            GROUP BY genre
            ORDER BY plays DESC
            """
        )
    ]


def month_year(conn) -> dict:
    """Sum of plays grouped by year-month of last_played.
    Shape: { "2026": { "01": 412, "02": 389, ... }, ... }
    """
    out: dict[str, dict[str, int]] = {}
    for r in conn.execute(
        """
        SELECT substr(last_played, 1, 4) AS y,
               substr(last_played, 6, 2) AS m,
               SUM(plays) AS p
        FROM tracks_current
        WHERE last_played IS NOT NULL
        GROUP BY y, m
        ORDER BY y, m
        """
    ):
        out.setdefault(r["y"], {})[r["m"]] = r["p"]
    return out


def year_artist(conn) -> list:
    """Most-played artist each year (by plays on songs last-played that year)."""
    rows = conn.execute(
        """
        WITH base AS (
            SELECT substr(last_played, 1, 4) AS year,
                   artist,
                   SUM(plays) AS plays
            FROM tracks_current
            WHERE last_played IS NOT NULL AND artist <> ''
            GROUP BY year, artist
        ),
        ranked AS (
            SELECT year, artist, plays,
                   ROW_NUMBER() OVER (PARTITION BY year ORDER BY plays DESC, artist) AS rn
            FROM base
        )
        SELECT year, artist, plays
        FROM ranked
        WHERE rn = 1
        ORDER BY year DESC
        """
    ).fetchall()
    return [{"year": r["year"], "artist": r["artist"], "plays": r["plays"]} for r in rows]


def country_year(conn) -> dict:
    """Sum of plays grouped by (year, country).
    Shape: { "2026": [{"country":"FR","plays":...}, ...], ... }
    """
    out: dict[str, list] = {}
    for r in conn.execute(
        """
        SELECT substr(t.last_played, 1, 4) AS year,
               ac.country AS country,
               SUM(t.plays) AS plays
        FROM tracks_current t
        JOIN artist_country ac ON ac.artist = t.artist
        WHERE t.last_played IS NOT NULL
        GROUP BY year, country
        ORDER BY year DESC, plays DESC
        """
    ):
        out.setdefault(r["year"], []).append({"country": r["country"], "plays": r["plays"]})
    return out


def tracks(conn) -> list:
    """Full track list for the searchable tracker page."""
    return [
        {
            "song": r["song"],
            "artist": r["artist"],
            "album": r["album"],
            "plays": r["plays"],
            "duration_sec": r["duration_sec"],
            "date_added": r["date_added"],
            "last_played": r["last_played"],
            "genre": r["genre"],
            "country": r["country"],
        }
        for r in conn.execute(
            """
            SELECT t.song, t.artist, t.album, t.plays, t.duration_sec,
                   t.date_added, t.last_played, t.genre, ac.country
            FROM tracks_current t
            LEFT JOIN artist_country ac ON ac.artist = t.artist
            ORDER BY t.plays DESC, t.artist, t.song
            """
        )
    ]


def pending_artists(conn) -> list:
    return [
        {"artist": r["artist"], "attempts": r["attempts"], "last_error": r["last_error"]}
        for r in conn.execute(
            "SELECT artist, attempts, last_error FROM artist_country_pending ORDER BY artist"
        )
    ]


# ---------- Main ----------

def main() -> None:
    conn = connect()
    print("build_data: emitting aggregates...")
    AGGREGATES_DIR.mkdir(parents=True, exist_ok=True)

    _write("kpis", kpis(conn))
    _write("top_artists", top_artists(conn))
    _write("country_plays", country_plays(conn))
    _write("genre_plays", genre_plays(conn))
    _write("month_year", month_year(conn))
    _write("year_artist", year_artist(conn))
    _write("country_year", country_year(conn))
    _write("tracks", tracks(conn))
    _write("pending_artists", pending_artists(conn))
    _write_combined()

    print("build_data: done")


if __name__ == "__main__":
    main()
