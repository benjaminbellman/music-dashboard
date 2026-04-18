"""SQLite schema + connection helpers for the music dashboard."""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "music.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks_current (
    track_id        TEXT PRIMARY KEY,
    song            TEXT NOT NULL,
    artist          TEXT NOT NULL,
    primary_artist  TEXT NOT NULL DEFAULT '',
    album           TEXT,
    duration_sec    INTEGER,
    plays           INTEGER NOT NULL DEFAULT 0,
    date_added      TEXT,
    last_played     TEXT,
    genre           TEXT
);

CREATE INDEX IF NOT EXISTS idx_tracks_artist         ON tracks_current(artist);
CREATE INDEX IF NOT EXISTS idx_tracks_primary_artist ON tracks_current(primary_artist);
CREATE INDEX IF NOT EXISTS idx_tracks_genre          ON tracks_current(genre);

-- One row per credited artist on a track. Used to give The Weeknd credit on
-- "Doja Cat & The Weeknd" while keeping primary_artist for country totals
-- (which would otherwise double-count plays).
CREATE TABLE IF NOT EXISTS track_artists (
    track_id  TEXT NOT NULL,
    artist    TEXT NOT NULL,
    PRIMARY KEY (track_id, artist)
);

CREATE INDEX IF NOT EXISTS idx_track_artists_artist ON track_artists(artist);

CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_date TEXT NOT NULL,
    track_id      TEXT NOT NULL,
    plays         INTEGER NOT NULL,
    last_played   TEXT,
    PRIMARY KEY (snapshot_date, track_id)
);

CREATE TABLE IF NOT EXISTS artist_country (
    artist   TEXT PRIMARY KEY,
    country  TEXT NOT NULL,
    source   TEXT NOT NULL,
    updated  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artist_country_pending (
    artist      TEXT PRIMARY KEY,
    first_seen  TEXT NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 1,
    last_error  TEXT
);
"""


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def today_iso() -> str:
    return dt.date.today().isoformat()
