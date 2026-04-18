#!/usr/bin/env python3
"""Populate artist_country via three modes:

  python enrich.py --import-csv PATH     # one-off bulk seed (source='seed')
  python enrich.py                       # auto: MusicBrainz for pending artists
  python enrich.py --interactive         # prompt for each remaining unresolved artist

MusicBrainz policy: 1 req/sec, polite User-Agent. See
https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

import musicbrainzngs

from db import connect, now_iso

USER_AGENT = ("music-dashboard", "0.2.0", "https://github.com/benjaminbellman")
MIN_SCORE = 90  # MusicBrainz returns 0-100 match score

# ISO alpha-2 codes we accept from user input in interactive mode.
# MusicBrainz returns alpha-2 natively.
VALID_ALPHA2 = set()  # filled on first use from iso3166 table below


def _load_valid_alpha2() -> set[str]:
    # Minimal; MusicBrainz only emits valid ISO codes so we don't need a full table
    # for the auto path. For interactive we accept anything 2 letters uppercased.
    return set()  # accept any 2-letter code in interactive mode


# ---------- Import CSV ----------

def cmd_import_csv(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"CSV not found: {path}")

    conn = connect()
    ts = now_iso()
    inserted = 0
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = [(r["artist"], r["country"].upper(), "seed", ts) for r in reader if r["artist"] and r["country"]]
    with conn:
        # Only insert if we don't have a better entry already.
        for artist, country, source, updated in rows:
            cur = conn.execute("SELECT source FROM artist_country WHERE artist = ?", (artist,))
            existing = cur.fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO artist_country (artist, country, source, updated) VALUES (?,?,?,?)",
                    (artist, country, source, updated),
                )
                inserted += 1
        # Clear any pending rows that are now satisfied
        conn.execute("DELETE FROM artist_country_pending WHERE artist IN (SELECT artist FROM artist_country)")
    print(f"imported {inserted:,} artist→country rows from {path}")


# ---------- MusicBrainz auto-lookup ----------

def _mb_lookup(artist: str) -> tuple[str | None, str | None]:
    """Return (country_alpha2, error)."""
    try:
        res = musicbrainzngs.search_artists(artist=artist, limit=3)
    except Exception as e:  # network errors, rate limits, etc.
        return None, f"mb error: {e}"
    candidates = res.get("artist-list", [])
    if not candidates:
        return None, "no match"
    for cand in candidates:
        score = int(cand.get("ext:score", "0"))
        name = cand.get("name", "")
        country = cand.get("country")
        if score >= MIN_SCORE and name.lower() == artist.lower() and country:
            return country, None
    return None, f"no confident match (best score={candidates[0].get('ext:score')})"


def cmd_auto() -> None:
    musicbrainzngs.set_useragent(*USER_AGENT)
    conn = connect()

    pending = conn.execute("SELECT artist FROM artist_country_pending ORDER BY artist").fetchall()
    if not pending:
        print("enrich: nothing pending")
        return

    print(f"enrich: looking up {len(pending)} artists on MusicBrainz (1 req/sec)")
    resolved = 0
    for (artist,) in pending:
        country, err = _mb_lookup(artist)
        ts = now_iso()
        if country:
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO artist_country (artist, country, source, updated) "
                    "VALUES (?,?, 'musicbrainz', ?)",
                    (artist, country, ts),
                )
                conn.execute("DELETE FROM artist_country_pending WHERE artist = ?", (artist,))
            resolved += 1
            print(f"  ✓ {artist} → {country}")
        else:
            with conn:
                conn.execute(
                    "UPDATE artist_country_pending SET attempts = attempts + 1, last_error = ? "
                    "WHERE artist = ?",
                    (err, artist),
                )
            print(f"  · {artist} ({err})")
        time.sleep(1.05)  # MB rate limit

    print(f"enrich: {resolved}/{len(pending)} resolved via MusicBrainz")


# ---------- Interactive ----------

def cmd_interactive() -> None:
    conn = connect()
    pending = conn.execute(
        "SELECT artist, attempts, last_error FROM artist_country_pending ORDER BY artist"
    ).fetchall()
    if not pending:
        print("enrich: nothing pending")
        return

    print(f"enrich: {len(pending)} artists need manual country assignment.")
    print("  Enter ISO alpha-2 code (US, FR, GB, ...) — or 'skip' / 'quit'\n")

    for row in pending:
        artist = row["artist"]
        hint = f"  (tried {row['attempts']}×: {row['last_error']})" if row["last_error"] else ""
        while True:
            resp = input(f"  {artist}?{hint}\n    > ").strip().upper()
            if resp == "QUIT":
                return
            if resp in ("", "SKIP"):
                break
            if len(resp) == 2 and resp.isalpha():
                ts = now_iso()
                with conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO artist_country (artist, country, source, updated) "
                        "VALUES (?,?, 'manual', ?)",
                        (artist, resp, ts),
                    )
                    conn.execute("DELETE FROM artist_country_pending WHERE artist = ?", (artist,))
                print(f"    saved: {artist} → {resp}")
                break
            print("    (need 2 letters, e.g. 'US', 'FR', 'GB')")


# ---------- Main ----------

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--import-csv", type=Path, help="bulk import a seed CSV (artist,country)")
    p.add_argument("--interactive", action="store_true", help="prompt for each pending artist")
    args = p.parse_args()

    if args.import_csv:
        cmd_import_csv(args.import_csv)
    elif args.interactive:
        cmd_interactive()
    else:
        cmd_auto()


if __name__ == "__main__":
    main()
