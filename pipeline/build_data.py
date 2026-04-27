#!/usr/bin/env python3
"""Read data/music.db and emit pre-computed JSON aggregates under data/aggregates/.

Each JSON is a 1:1 map to a dashboard view, so we can verify values against
the legacy Excel dashboard on a per-view basis.
"""

from __future__ import annotations

import datetime as dt
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from db import connect

ROOT = Path(__file__).resolve().parent.parent
AGGREGATES_DIR = ROOT / "data" / "aggregates"
DASHBOARD_DATA = ROOT / "docs" / "data"

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
    """Top artists by song count and total plays. Uses track_artists so an
    artist gets credit for every track they're listed on — features included.
    'Doja Cat & The Weeknd' counts both Doja Cat and The Weeknd."""
    by_song = [
        {"rank": i + 1, "artist": r["artist"], "count": r["c"], "country": r["country"]}
        for i, r in enumerate(conn.execute(
            """
            SELECT ta.artist, COUNT(DISTINCT ta.track_id) AS c, ac.country
            FROM track_artists ta
            LEFT JOIN artist_country ac ON ac.artist = ta.artist
            WHERE ta.artist <> ''
            GROUP BY ta.artist
            ORDER BY c DESC, ta.artist
            LIMIT 50
            """
        ))
    ]
    by_plays = [
        {"rank": i + 1, "artist": r["artist"], "plays": r["p"], "country": r["country"]}
        for i, r in enumerate(conn.execute(
            """
            SELECT ta.artist, SUM(t.plays) AS p, ac.country
            FROM track_artists ta
            JOIN tracks_current t ON t.track_id = ta.track_id
            LEFT JOIN artist_country ac ON ac.artist = ta.artist
            WHERE ta.artist <> ''
            GROUP BY ta.artist
            ORDER BY p DESC, ta.artist
            LIMIT 50
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
                   SUM(t.plays)                       AS plays,
                   COUNT(DISTINCT t.primary_artist)   AS artists,
                   COUNT(*)                           AS songs
            FROM tracks_current t
            JOIN artist_country ac ON ac.artist = t.primary_artist
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
    """Sum of plays grouped by year-month a song was added to the library.
    Shape: { "2026": { "01": 412, "02": 389, ... }, ... }

    Rationale: Apple Music gives us lifetime plays + when the song entered
    the library. Most plays of a song happen in the months after it's added
    (the "honeymoon" effect), so date_added is a much better proxy for "when
    were you listening to this" than last_played would be.
    """
    out: dict[str, dict[str, int]] = {}
    for r in conn.execute(
        """
        SELECT substr(date_added, 1, 4) AS y,
               substr(date_added, 6, 2) AS m,
               SUM(plays) AS p
        FROM tracks_current
        WHERE date_added IS NOT NULL
        GROUP BY y, m
        ORDER BY y, m
        """
    ):
        out.setdefault(r["y"], {})[r["m"]] = r["p"]
    return out


def year_artist(conn) -> list:
    """Most-credited artist per year-added (lifetime plays summed across songs
    added in that year, with credit going to every artist in the artist
    column — features included)."""
    rows = conn.execute(
        """
        WITH base AS (
            SELECT substr(t.date_added, 1, 4) AS year,
                   ta.artist                  AS artist,
                   SUM(t.plays)               AS plays
            FROM track_artists ta
            JOIN tracks_current t ON t.track_id = ta.track_id
            WHERE t.date_added IS NOT NULL AND ta.artist <> ''
            GROUP BY year, ta.artist
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


def genre_year(conn) -> dict:
    """Plays aggregated by (year_added, genre). Top 8 genres kept explicitly,
    everything else bucketed as 'Other' so a stacked area chart stays readable.
    Shape: [{year, genre, plays}, ...]
    """
    # Top 8 genres by total plays across all time
    top = [
        r["genre"] for r in conn.execute(
            """SELECT COALESCE(NULLIF(genre,''),'Unspecified') AS genre, SUM(plays) p
               FROM tracks_current GROUP BY genre ORDER BY p DESC LIMIT 8"""
        )
    ]
    rows = list(conn.execute(
        """
        SELECT substr(date_added, 1, 4) AS year,
               COALESCE(NULLIF(genre, ''), 'Unspecified') AS genre,
               SUM(plays) AS plays
        FROM tracks_current
        WHERE date_added IS NOT NULL
        GROUP BY year, genre
        ORDER BY year, genre
        """
    ))
    # Bucket non-top into "Other"
    agg: dict[tuple[str, str], int] = {}
    for r in rows:
        key = (r["year"], r["genre"] if r["genre"] in top else "Other")
        agg[key] = agg.get(key, 0) + r["plays"]
    return [
        {"year": int(y), "genre": g, "plays": p}
        for (y, g), p in sorted(agg.items())
    ]


def country_year(conn) -> dict:
    """Sum of plays grouped by (year-added, country)."""
    out: dict[str, list] = {}
    for r in conn.execute(
        """
        SELECT substr(t.date_added, 1, 4) AS year,
               ac.country AS country,
               SUM(t.plays) AS plays
        FROM tracks_current t
        JOIN artist_country ac ON ac.artist = t.primary_artist
        WHERE t.date_added IS NOT NULL
        GROUP BY year, country
        ORDER BY year DESC, plays DESC
        """
    ):
        out.setdefault(r["year"], []).append({"country": r["country"], "plays": r["plays"]})
    return out


def tracks(conn) -> list:
    """Full track list for the searchable tracker + drill-downs.
    Includes a `credits` array of every credited artist on each track so
    the client can filter by artist regardless of who's listed first."""
    credits_by_track: dict[str, list[str]] = {}
    for r in conn.execute("SELECT track_id, artist FROM track_artists"):
        credits_by_track.setdefault(r["track_id"], []).append(r["artist"])

    return [
        {
            "id": r["track_id"],
            "song": r["song"],
            "artist": r["artist"],
            "credits": credits_by_track.get(r["track_id"], []),
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
            SELECT t.track_id, t.song, t.artist, t.album, t.plays, t.duration_sec,
                   t.date_added, t.last_played, t.genre, ac.country
            FROM tracks_current t
            LEFT JOIN artist_country ac ON ac.artist = t.primary_artist
            ORDER BY t.plays DESC, t.artist, t.song
            """
        )
    ]


def play_history(conn) -> dict:
    """Forward-looking listening trends, derived from snapshot deltas.

    Apple Music only exposes a lifetime play counter and a single
    `last_played` timestamp per track — there's no per-play log. We can't
    reconstruct play history before this project started, but we CAN
    capture it going forward by snapshotting the counter on every sync
    and diffing consecutive snapshots.

    Emits:
      - daily: [{date, plays}]  — total plays in each sync window,
        labeled by snapshot date. With daily syncs this becomes a
        per-day plays chart.
      - windows: keyed by "14d" / "30d" / "90d" / "180d" / "365d".
        Each window holds top artists / genres / tracks ranked by plays
        within the window. Multi-artist credit (track_artists) so a
        feature counts for both artists.
      - latest_snapshot, first_snapshot, snapshot_count: metadata so the
        UI can tell the user "you have N days of trend data so far."
    """
    snapshots = conn.execute(
        "SELECT snapshot_date, track_id, plays FROM snapshots "
        "ORDER BY track_id, snapshot_date"
    ).fetchall()

    if not snapshots:
        return {"daily": [], "windows": {}, "snapshot_count": 0}

    # Compute per-track positive deltas between consecutive snapshots.
    # (date, track_id, delta) for each window where the count grew.
    deltas: list[tuple[str, str, int]] = []
    last_plays: dict = {}
    for r in snapshots:
        tid, date, plays = r["track_id"], r["snapshot_date"], r["plays"]
        prev = last_plays.get(tid)
        if prev is not None:
            d = plays - prev
            if d > 0:
                deltas.append((date, tid, d))
        last_plays[tid] = plays

    # Track metadata, indexed by track_id.
    track_meta: dict = {}
    for r in conn.execute(
        "SELECT track_id, song, artist, primary_artist, genre FROM tracks_current"
    ):
        track_meta[r["track_id"]] = dict(r)

    # Multi-artist credit: track_id → list of credited artists.
    credits_by_track: dict = defaultdict(list)
    for r in conn.execute("SELECT track_id, artist FROM track_artists"):
        credits_by_track[r["track_id"]].append(r["artist"])

    # Daily timeline: total plays per snapshot date. (Snapshots whose delta
    # is computed from a multi-day gap will pile up on the later date —
    # that's the honest representation, no fake interpolation.)
    daily_totals: Counter = Counter()
    for date, _tid, d in deltas:
        daily_totals[date] += d
    daily = [{"date": d, "plays": p} for d, p in sorted(daily_totals.items())]

    snapshot_dates = sorted({r["snapshot_date"] for r in snapshots})
    latest = snapshot_dates[-1]
    today = dt.date.fromisoformat(latest)

    windows = {}
    for label, days in [("14d", 14), ("30d", 30), ("90d", 90), ("180d", 180), ("365d", 365)]:
        cutoff = (today - dt.timedelta(days=days)).isoformat()
        artist_plays: Counter = Counter()
        genre_plays: Counter = Counter()
        track_plays: Counter = Counter()
        track_meta_for_top: dict = {}
        for date, tid, d in deltas:
            if date <= cutoff:
                continue
            meta = track_meta.get(tid)
            if not meta:
                continue
            # Credit every artist on the track (matches top_artists logic).
            credited = credits_by_track.get(tid) or [meta["primary_artist"] or meta["artist"]]
            for a in credited:
                if a:
                    artist_plays[a] += d
            genre_plays[meta.get("genre") or "Unspecified"] += d
            key = (meta["song"], meta["artist"])
            track_plays[key] += d
            track_meta_for_top[key] = meta

        total = sum(daily_totals[d] for d in daily_totals if d > cutoff)
        windows[label] = {
            "days": days,
            "total_plays": total,
            "top_artists": [
                {"artist": a, "plays": p}
                for a, p in artist_plays.most_common(25)
            ],
            "top_genres": [
                {"genre": g, "plays": p}
                for g, p in genre_plays.most_common(25)
            ],
            "top_tracks": [
                {
                    "song": s, "artist": a, "plays": p,
                    "genre": track_meta_for_top[(s, a)].get("genre") or "Unspecified",
                }
                for (s, a), p in track_plays.most_common(25)
            ],
        }

    return {
        "daily": daily,
        "windows": windows,
        "first_snapshot": snapshot_dates[0],
        "latest_snapshot": latest,
        "snapshot_count": len(snapshot_dates),
    }


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
    _write("genre_year", genre_year(conn))
    _write("tracks", tracks(conn))
    _write("play_history", play_history(conn))
    _write("pending_artists", pending_artists(conn))
    _write_combined()

    print("build_data: done")


if __name__ == "__main__":
    main()
