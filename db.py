from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from .models import Insights, Offer

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    checked_at TEXT NOT NULL,          -- UTC ISO
    provider TEXT NOT NULL,
    n_queries INTEGER NOT NULL,
    n_errors INTEGER NOT NULL,
    errors TEXT
);
CREATE TABLE IF NOT EXISTS offers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    checked_at TEXT NOT NULL,
    dest_group TEXT NOT NULL,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    outbound_date TEXT NOT NULL,
    return_date TEXT,
    return_to TEXT,                     -- lotnisko powrotu (np. KRK)
    price REAL NOT NULL,
    currency TEXT NOT NULL,
    airlines TEXT,
    flight_numbers TEXT,
    stops INTEGER,
    total_duration_min INTEGER,
    departure_time TEXT,
    arrival_time TEXT,
    rank INTEGER                        -- 1..top_n gdy w top, inaczej NULL
);
CREATE INDEX IF NOT EXISTS ix_offers_route ON offers(origin, destination, outbound_date, return_date);
CREATE INDEX IF NOT EXISTS ix_offers_run ON offers(run_id);
CREATE TABLE IF NOT EXISTS insights (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    checked_at TEXT NOT NULL,
    dest_group TEXT NOT NULL,
    outbound_date TEXT NOT NULL,
    return_date TEXT,
    lowest_price REAL,
    price_level TEXT,
    typical_low REAL,
    typical_high REAL
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def save_run(con, provider: str, offers: list[Offer], insights: list[Insights],
             n_queries: int, errors: list[str], checked_at: datetime) -> int:
    ts = checked_at.isoformat(timespec="seconds")
    with con:
        cur = con.execute(
            "INSERT INTO runs(checked_at, provider, n_queries, n_errors, errors) VALUES (?,?,?,?,?)",
            (ts, provider, n_queries, len(errors), "\n".join(errors) or None))
        run_id = cur.lastrowid
        con.executemany(
            """INSERT INTO offers(run_id, checked_at, dest_group, origin, destination, outbound_date,
               return_date, return_to, price, currency, airlines, flight_numbers, stops, total_duration_min,
               departure_time, arrival_time, rank) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(run_id, ts, o.dest_group, o.origin, o.destination, o.outbound_date.isoformat(),
              o.return_date.isoformat() if o.return_date else None, o.return_to, o.price, o.currency,
              o.airlines, o.flight_numbers, o.stops, o.total_duration_min,
              o.departure_time, o.arrival_time, o.rank) for o in offers])
        con.executemany(
            """INSERT INTO insights(run_id, checked_at, dest_group, outbound_date, return_date,
               lowest_price, price_level, typical_low, typical_high) VALUES (?,?,?,?,?,?,?,?,?)""",
            [(run_id, ts, i.dest_group, i.outbound_date.isoformat(),
              i.return_date.isoformat() if i.return_date else None,
              i.lowest_price, i.price_level, i.typical_low, i.typical_high) for i in insights])
    return run_id
