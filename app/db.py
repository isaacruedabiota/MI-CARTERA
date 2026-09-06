"""Capa SQLite. Sin ORM: el volumen de datos es minusculo y la Pi lo agradece."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS instruments (
    ticker      TEXT PRIMARY KEY,
    name        TEXT,
    currency    TEXT,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS portfolio (
    ticker     TEXT PRIMARY KEY,
    added_at   TEXT NOT NULL,
    alias      TEXT,
    topic      TEXT
);

CREATE TABLE IF NOT EXISTS snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker      TEXT NOT NULL,
    day         TEXT NOT NULL,
    price       REAL,
    prev_close  REAL,
    change_pct  REAL,
    currency    TEXT,
    source      TEXT,
    asof        TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE (ticker, day)
);

CREATE TABLE IF NOT EXISTS news_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker       TEXT NOT NULL,
    day          TEXT NOT NULL,
    title        TEXT NOT NULL,
    publisher    TEXT,
    url          TEXT,
    published_at TEXT,
    source_feed  TEXT,
    created_at   TEXT NOT NULL,
    UNIQUE (ticker, day, title)
);

CREATE TABLE IF NOT EXISTS explanations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker      TEXT NOT NULL,
    day         TEXT NOT NULL,
    summary     TEXT NOT NULL,
    status      TEXT NOT NULL,
    provider    TEXT,
    model       TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE (ticker, day)
);

CREATE TABLE IF NOT EXISTS job_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT NOT NULL,
    detail       TEXT
);

CREATE INDEX IF NOT EXISTS idx_snapshots_day ON snapshots (day);
CREATE INDEX IF NOT EXISTS idx_news_ticker_day ON news_items (ticker, day);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _db_file() -> Path:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    return settings.db_path


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(_db_file(), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Anade columnas nuevas a bases de datos ya creadas."""
    columnas = {row["name"] for row in conn.execute("PRAGMA table_info(portfolio)")}
    if "alias" not in columnas:
        conn.execute("ALTER TABLE portfolio ADD COLUMN alias TEXT")
    if "topic" not in columnas:
        conn.execute("ALTER TABLE portfolio ADD COLUMN topic TEXT")


# --- cartera ----------------------------------------------------------------

def get_portfolio() -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT ticker FROM portfolio ORDER BY ticker"
        ).fetchall()
    return [r["ticker"] for r in rows]


def get_portfolio_aliases() -> dict[str, str | None]:
    """Ticker -> nombre puesto a mano por el usuario (None si no tiene)."""
    with connect() as conn:
        rows = conn.execute("SELECT ticker, alias FROM portfolio").fetchall()
    return {r["ticker"]: r["alias"] for r in rows}


def get_portfolio_topics() -> dict[str, str | None]:
    """Ticker -> tema de noticias escrito a mano (None si se deduce solo)."""
    with connect() as conn:
        rows = conn.execute("SELECT ticker, topic FROM portfolio").fetchall()
    return {r["ticker"]: r["topic"] for r in rows}


def set_topic(ticker: str, topic: str | None) -> bool:
    with connect() as conn:
        cur = conn.execute(
            "UPDATE portfolio SET topic = ? WHERE ticker = ?", (topic or None, ticker)
        )
        return cur.rowcount > 0


def set_alias(ticker: str, alias: str | None) -> bool:
    """Renombra un valor de la cartera. alias vacio = vuelve al nombre oficial."""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE portfolio SET alias = ? WHERE ticker = ?",
            (alias or None, ticker),
        )
        return cur.rowcount > 0


def add_ticker(ticker: str) -> bool:
    """Devuelve True si se ha anadido, False si ya estaba."""
    with connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO portfolio (ticker, added_at) VALUES (?, ?)",
            (ticker, now_iso()),
        )
        return cur.rowcount > 0


def remove_ticker(ticker: str) -> bool:
    """Quita el valor de la cartera. El historico se conserva."""
    with connect() as conn:
        cur = conn.execute("DELETE FROM portfolio WHERE ticker = ?", (ticker,))
        return cur.rowcount > 0


def seed_portfolio(tickers: list[str]) -> None:
    """Primera puesta en marcha: la cartera parte de TICKERS del .env."""
    if not tickers or get_portfolio():
        return
    with connect() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO portfolio (ticker, added_at) VALUES (?, ?)",
            [(t, now_iso()) for t in tickers],
        )


# --- escrituras -------------------------------------------------------------

def upsert_instrument(ticker: str, name: str | None, currency: str | None) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO instruments (ticker, name, currency, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                name = COALESCE(excluded.name, instruments.name),
                currency = COALESCE(excluded.currency, instruments.currency),
                updated_at = excluded.updated_at
            """,
            (ticker, name, currency, now_iso()),
        )


def get_instruments() -> dict[str, dict[str, str | None]]:
    with connect() as conn:
        rows = conn.execute("SELECT ticker, name, currency FROM instruments").fetchall()
    return {r["ticker"]: {"name": r["name"], "currency": r["currency"]} for r in rows}


def save_snapshot(
    ticker: str,
    day: str,
    price: float | None,
    prev_close: float | None,
    change_pct: float | None,
    currency: str | None,
    source: str,
    asof: str | None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO snapshots (ticker, day, price, prev_close, change_pct,
                                   currency, source, asof, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, day) DO UPDATE SET
                price = excluded.price,
                prev_close = excluded.prev_close,
                change_pct = excluded.change_pct,
                currency = excluded.currency,
                source = excluded.source,
                asof = excluded.asof,
                created_at = excluded.created_at
            """,
            (ticker, day, price, prev_close, change_pct, currency, source, asof, now_iso()),
        )


def snapshot_has_price(ticker: str, day: str) -> bool:
    """Evita que una ejecucion fallida machaque datos buenos ya guardados."""
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM snapshots WHERE ticker = ? AND day = ? AND price IS NOT NULL",
            (ticker, day),
        ).fetchone()
    return row is not None


def explanation_exists(ticker: str, day: str) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM explanations WHERE ticker = ? AND day = ?", (ticker, day)
        ).fetchone()
    return row is not None


def get_explanation_status(ticker: str, day: str) -> str | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT status FROM explanations WHERE ticker = ? AND day = ?", (ticker, day)
        ).fetchone()
    return row["status"] if row else None


def save_news(ticker: str, day: str, items: list[dict[str, Any]]) -> None:
    rows = [
        (
            ticker,
            day,
            str(item.get("title", ""))[:500],
            item.get("publisher"),
            item.get("url"),
            item.get("published_at"),
            item.get("source_feed"),
            now_iso(),
        )
        for item in items
        if item.get("title")
    ]
    if not rows:
        return
    with connect() as conn:
        conn.executemany(
            """
            INSERT OR IGNORE INTO news_items
                (ticker, day, title, publisher, url, published_at, source_feed, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def save_explanation(
    ticker: str,
    day: str,
    summary: str,
    status: str,
    provider: str | None,
    model: str | None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO explanations (ticker, day, summary, status, provider, model, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, day) DO UPDATE SET
                summary = excluded.summary,
                status = excluded.status,
                provider = excluded.provider,
                model = excluded.model,
                created_at = excluded.created_at
            """,
            (ticker, day, summary, status, provider, model, now_iso()),
        )


def start_job_run() -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO job_runs (started_at, status) VALUES (?, ?)",
            (now_iso(), "running"),
        )
        return int(cur.lastrowid)


def finish_job_run(run_id: int, status: str, detail: str = "") -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE job_runs SET finished_at = ?, status = ?, detail = ? WHERE id = ?",
            (now_iso(), status, detail[:2000], run_id),
        )


# --- lecturas ---------------------------------------------------------------

def latest_day() -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT MAX(day) AS day FROM snapshots").fetchone()
    return row["day"] if row and row["day"] else None


def available_days(limit: int = 60) -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT day FROM snapshots ORDER BY day DESC LIMIT ?", (limit,)
        ).fetchall()
    return [r["day"] for r in rows]


def portfolio_for_day(day: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT s.ticker, s.day, s.price, s.prev_close, s.change_pct, s.currency,
                   s.source, s.asof, s.created_at,
                   i.name AS name,
                   e.summary AS summary, e.status AS explanation_status,
                   e.provider AS provider, e.model AS model
            FROM snapshots s
            LEFT JOIN instruments i ON i.ticker = s.ticker
            LEFT JOIN explanations e ON e.ticker = s.ticker AND e.day = s.day
            WHERE s.day = ?
            ORDER BY ABS(COALESCE(s.change_pct, 0)) DESC, s.ticker
            """,
            (day,),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            news = conn.execute(
                """
                SELECT title, publisher, url, published_at
                FROM news_items WHERE ticker = ? AND day = ?
                ORDER BY published_at DESC LIMIT 10
                """,
                (row["ticker"], day),
            ).fetchall()
            item["news"] = [dict(n) for n in news]
            result.append(item)
    return result


def last_job_run() -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM job_runs ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None
