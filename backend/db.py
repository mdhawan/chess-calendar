from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Iterator

from . import geo
from .models import Tournament

DB_PATH = Path(__file__).parent / "data" / "chess.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tournaments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source TEXT NOT NULL,
    source_url TEXT,
    start_date TEXT NOT NULL,
    end_date TEXT,
    registration_deadline TEXT,
    city TEXT,
    state TEXT,
    country TEXT,
    venue TEXT,
    latitude REAL,
    longitude REAL,
    geo_precision TEXT,
    format TEXT,
    time_control TEXT,
    rounds INTEGER,
    is_fide_rated INTEGER,
    is_aicf_rated INTEGER,
    age_categories TEXT,
    rating_categories TEXT,
    open_to_all INTEGER,
    entry_fee_inr INTEGER,
    prize_fund_inr INTEGER,
    expected_field_size INTEGER,
    organizer TEXT,
    contact TEXT,
    registration_url TEXT,
    raw_text TEXT,
    fetched_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_tournaments_start ON tournaments(start_date);
CREATE INDEX IF NOT EXISTS idx_tournaments_state ON tournaments(state);
CREATE INDEX IF NOT EXISTS idx_tournaments_source ON tournaments(source);

CREATE TABLE IF NOT EXISTS subscriptions (
    endpoint TEXT PRIMARY KEY,
    keys_json TEXT NOT NULL,
    states_filter TEXT,
    fide_only INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS refresh_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT,
    finished_at TEXT,
    source TEXT,
    rows_in INTEGER,
    error TEXT
);
"""


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        _repair_misaligned(conn)
    purge_past()
    backfill_geo()


def _repair_misaligned(conn: sqlite3.Connection) -> int:
    """Clean up rows corrupted by the old column-misaligned INSERT.

    Before ``upsert_tournaments`` bound values by name, a migrated DB (geo
    columns appended at the end, not in SCHEMA position) shifted every value
    from ``format`` onward into the wrong column — e.g. a latitude landed in
    ``format`` and a timestamp in ``geo_precision``. The intact columns (id,
    dates, city, state) are enough: we null the scrambled fields so nothing
    wrong is shown, ``backfill_geo`` recomputes coordinates from city/state, and
    the next refresh repopulates format/rounds/etc.

    Two signatures catch the corruption:
      * ``format`` holds something other than the known enum values (a value
        shifted in from an earlier column), or
      * ``age_categories`` isn't a JSON array — it must always be
        ``json.dumps(list)``, so a scalar there (a ``rounds`` integer that
        shifted right while ``format`` shifted to NULL) is corruption the enum
        check alone misses. This scalar is what made ``ageSort`` throw on the
        client and blanked the calendar.
    """
    cur = conn.execute(
        "UPDATE tournaments "
        "SET format=NULL, time_control=NULL, rounds=NULL, is_fide_rated=NULL, "
        "    is_aicf_rated=NULL, age_categories='[]', rating_categories='[]', "
        "    latitude=NULL, longitude=NULL, geo_precision=NULL "
        "WHERE (format IS NOT NULL AND format NOT IN ('rapid','blitz','classical')) "
        "   OR age_categories IS NULL OR age_categories NOT LIKE '[%'"
    )
    return cur.rowcount


def purge_past() -> int:
    """Delete finished events. We neither display nor fetch past tournaments, so
    they only bloat the DB; this clears any left over from an earlier build.
    """
    today = date.today().isoformat()
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM tournaments "
            "WHERE COALESCE(end_date, start_date) < ?",
            (today,),
        )
        return cur.rowcount


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the initial schema.

    ``CREATE TABLE IF NOT EXISTS`` won't alter an existing table, so any column
    added later must be introduced with ALTER TABLE guarded by table_info.
    """
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(tournaments)")}
    for col, decl in (("latitude", "REAL"), ("longitude", "REAL"), ("geo_precision", "TEXT")):
        if col not in existing:
            conn.execute(f"ALTER TABLE tournaments ADD COLUMN {col} {decl}")


def backfill_geo() -> int:
    """Populate lat/lng/geo_precision (and canonicalise state) for rows missing
    coordinates. Idempotent and cheap when nothing needs backfilling — used so an
    existing DB gets map pins without a re-scrape.
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, city, state FROM tournaments "
            "WHERE latitude IS NULL AND (country = 'India' OR country IS NULL)"
        ).fetchall()
        n = 0
        for r in rows:
            lat, lng, precision = geo.geocode(r["city"], r["state"])
            if lat is None:
                continue
            canon = geo.canonical_state(r["state"], r["city"]) or r["state"]
            conn.execute(
                "UPDATE tournaments SET latitude=?, longitude=?, geo_precision=?, state=? WHERE id=?",
                (lat, lng, precision, canon, r["id"]),
            )
            n += 1
    return n


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    # timeout=30 + WAL let concurrent adapters (refresh runs them in parallel)
    # write without tripping over "database is locked"; readers never block
    # writers under WAL.
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_tournaments(rows: list[Tournament]) -> int:
    if not rows:
        return 0
    with connect() as conn:
        for t in rows:
            conn.execute(
                """
                INSERT INTO tournaments (
                    id, name, source, source_url,
                    start_date, end_date, registration_deadline,
                    city, state, country, venue,
                    latitude, longitude, geo_precision,
                    format, time_control, rounds,
                    is_fide_rated, is_aicf_rated,
                    age_categories, rating_categories, open_to_all,
                    entry_fee_inr, prize_fund_inr, expected_field_size,
                    organizer, contact, registration_url,
                    raw_text, fetched_at
                ) VALUES (
                    :id, :name, :source, :source_url,
                    :start_date, :end_date, :registration_deadline,
                    :city, :state, :country, :venue,
                    :latitude, :longitude, :geo_precision,
                    :format, :time_control, :rounds,
                    :is_fide_rated, :is_aicf_rated,
                    :age_categories, :rating_categories, :open_to_all,
                    :entry_fee_inr, :prize_fund_inr, :expected_field_size,
                    :organizer, :contact, :registration_url,
                    :raw_text, :fetched_at
                )
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    source_url=excluded.source_url,
                    start_date=excluded.start_date,
                    end_date=excluded.end_date,
                    registration_deadline=excluded.registration_deadline,
                    city=excluded.city,
                    state=excluded.state,
                    country=excluded.country,
                    venue=excluded.venue,
                    latitude=excluded.latitude,
                    longitude=excluded.longitude,
                    geo_precision=excluded.geo_precision,
                    format=excluded.format,
                    time_control=excluded.time_control,
                    rounds=excluded.rounds,
                    is_fide_rated=excluded.is_fide_rated,
                    is_aicf_rated=excluded.is_aicf_rated,
                    age_categories=excluded.age_categories,
                    rating_categories=excluded.rating_categories,
                    open_to_all=excluded.open_to_all,
                    entry_fee_inr=excluded.entry_fee_inr,
                    prize_fund_inr=excluded.prize_fund_inr,
                    expected_field_size=excluded.expected_field_size,
                    organizer=excluded.organizer,
                    contact=excluded.contact,
                    registration_url=excluded.registration_url,
                    raw_text=excluded.raw_text,
                    fetched_at=excluded.fetched_at
                """,
                _to_row(t),
            )
    return len(rows)


def _to_row(t: Tournament) -> dict:
    d = t.model_dump()
    d["start_date"] = t.start_date.isoformat()
    d["end_date"] = t.end_date.isoformat() if t.end_date else None
    d["registration_deadline"] = (
        t.registration_deadline.isoformat() if t.registration_deadline else None
    )
    d["age_categories"] = json.dumps(t.age_categories)
    d["rating_categories"] = json.dumps(t.rating_categories)
    d["is_fide_rated"] = int(t.is_fide_rated)
    d["is_aicf_rated"] = int(t.is_aicf_rated)
    d["open_to_all"] = int(t.open_to_all)
    d["fetched_at"] = t.fetched_at.isoformat()
    return d


def query_tournaments(
    start: str | None = None,
    end: str | None = None,
    state: str | None = None,
    states: list[str] | None = None,
    upcoming_only: bool = True,
    fide_only: bool = False,
    format: str | None = None,
    source: str | None = None,
) -> list[dict]:
    # Only ever surface Indian tournaments — this drops foreign chessbase.in
    # rows (ROU/PRT/HUN/...) that would otherwise pollute the state dropdown.
    sql = "SELECT * FROM tournaments WHERE country = 'India'"
    args: list = []
    if upcoming_only:
        # An event is "upcoming" if it hasn't finished yet.
        today = date.today().isoformat()
        sql += " AND (end_date >= ? OR (end_date IS NULL AND start_date >= ?))"
        args.extend([today, today])
    if start:
        sql += " AND (end_date IS NULL OR end_date >= ?) AND start_date >= ?"
        args.extend([start, start])
    if end:
        sql += " AND start_date <= ?"
        args.append(end)
    if state:
        sql += " AND LOWER(state) = LOWER(?)"
        args.append(state)
    if states:
        placeholders = ",".join("?" for _ in states)
        sql += f" AND LOWER(state) IN ({placeholders})"
        args.extend([s.lower() for s in states])
    if fide_only:
        sql += " AND is_fide_rated = 1"
    if format:
        sql += " AND LOWER(format) = LOWER(?)"
        args.append(format)
    if source:
        sql += " AND source = ?"
        args.append(source)
    sql += " ORDER BY start_date ASC"
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["age_categories"] = json.loads(d["age_categories"] or "[]")
        d["rating_categories"] = json.loads(d["rating_categories"] or "[]")
        d["is_fide_rated"] = bool(d["is_fide_rated"])
        d["is_aicf_rated"] = bool(d["is_aicf_rated"])
        d["open_to_all"] = bool(d["open_to_all"])
        out.append(d)
    return out


def cached_details(source: str) -> dict[str, dict]:
    """Return ``{id: {detail fields}}`` for rows already stored for ``source``.

    Lets an adapter skip re-fetching a per-tournament detail page it already
    resolved on a prior refresh. This is essential for chess-results, which caps
    each IP at 2000 requests/day: a full drill-down crawl is ~1500 requests, so
    reusing cached detail keeps steady-state refreshes to ~31 listing fetches
    plus a drilldown only for genuinely new tournaments.
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, start_date, end_date, city, venue, format, "
            "time_control, rounds, organizer, is_fide_rated "
            "FROM tournaments WHERE source = ?",
            (source,),
        ).fetchall()
    return {r["id"]: dict(r) for r in rows}


def log_refresh(source: str, started: str, finished: str, rows_in: int, error: str | None) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO refresh_log(started_at, finished_at, source, rows_in, error) VALUES (?,?,?,?,?)",
            (started, finished, source, rows_in, error),
        )


def latest_refresh() -> dict | None:
    with connect() as conn:
        r = conn.execute(
            "SELECT MAX(finished_at) AS finished_at FROM refresh_log WHERE error IS NULL"
        ).fetchone()
    return dict(r) if r and r["finished_at"] else None


def save_subscription(endpoint: str, keys: dict, states_filter: list[str], fide_only: bool) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO subscriptions(endpoint, keys_json, states_filter, fide_only)
            VALUES (?,?,?,?)
            ON CONFLICT(endpoint) DO UPDATE SET
                keys_json=excluded.keys_json,
                states_filter=excluded.states_filter,
                fide_only=excluded.fide_only
            """,
            (endpoint, json.dumps(keys), json.dumps(states_filter), int(fide_only)),
        )
