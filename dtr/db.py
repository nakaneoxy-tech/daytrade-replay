"""SQLite 蓄積層。1分足は (symbol, ts) を主キーに上書き保存する。"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import threading
from pathlib import Path

import pandas as pd

DB_PATH = Path(os.environ.get("DTR_DB", Path(__file__).resolve().parent.parent / "data" / "market.db"))
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
  symbol TEXT NOT NULL,
  ts     TEXT NOT NULL,           -- JST 'YYYY-MM-DD HH:MM'
  open REAL, high REAL, low REAL, close REAL, volume REAL,
  PRIMARY KEY (symbol, ts)
);
CREATE TABLE IF NOT EXISTS watchlist (
  symbol   TEXT PRIMARY KEY,
  added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
  symbol       TEXT PRIMARY KEY,
  last_updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS results (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  practiced_at TEXT NOT NULL,
  trade_date  TEXT NOT NULL,
  symbol      TEXT NOT NULL,
  pnl         REAL NOT NULL,
  commission  REAL NOT NULL,
  trips       INTEGER NOT NULL,
  wins        INTEGER NOT NULL,
  losses      INTEGER NOT NULL,
  detail      TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def now_jst() -> dt.datetime:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).replace(tzinfo=None)


def upsert_bars(symbol: str, df: pd.DataFrame) -> int:
    if df is None or not len(df):
        return 0
    rows = [
        (symbol, r.ts, r.open, r.high, r.low, r.close, r.volume)
        for r in df.itertuples(index=False)
    ]
    with _lock, connect() as con:
        con.executemany(
            "INSERT OR REPLACE INTO bars(symbol, ts, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)",
            rows,
        )
        con.execute(
            "INSERT OR REPLACE INTO meta(symbol, last_updated) VALUES (?,?)",
            (symbol, now_jst().strftime("%Y-%m-%d %H:%M:%S")),
        )
    return len(rows)


def last_updated(symbol: str) -> dt.datetime | None:
    with connect() as con:
        r = con.execute("SELECT last_updated FROM meta WHERE symbol=?", (symbol,)).fetchone()
    return dt.datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S") if r else None


def stored_dates(symbol: str) -> list[dict]:
    """蓄積済みの日付と、ザラ場時間内の本数。"""
    with connect() as con:
        rows = con.execute(
            """SELECT substr(ts,1,10) AS d, COUNT(*) AS n, SUM(volume) AS v FROM bars
               WHERE symbol=? AND substr(ts,12,5) BETWEEN '09:00' AND '15:30'
               GROUP BY d ORDER BY d DESC""",
            (symbol,),
        ).fetchall()
    return [{"date": r["d"], "bars": r["n"], "volume": r["v"] or 0} for r in rows]


def day_bars(symbol: str, date: str, start: str = "00:00", end: str = "23:59") -> list[dict]:
    with connect() as con:
        rows = con.execute(
            "SELECT ts, open, high, low, close, volume FROM bars WHERE symbol=? AND ts BETWEEN ? AND ? ORDER BY ts",
            (symbol, f"{date} {start}", f"{date} {end}"),
        ).fetchall()
    return [dict(r) for r in rows]


def prev_close(symbol: str, date: str) -> float | None:
    with connect() as con:
        r = con.execute(
            """SELECT close FROM bars WHERE symbol=? AND ts < ? AND substr(ts,12,5) BETWEEN '09:00' AND '15:30'
               ORDER BY ts DESC LIMIT 1""",
            (symbol, f"{date} 00:00"),
        ).fetchone()
    return float(r[0]) if r else None


def add_watch(symbol: str) -> None:
    with _lock, connect() as con:
        con.execute(
            "INSERT OR IGNORE INTO watchlist(symbol, added_at) VALUES (?,?)",
            (symbol, now_jst().strftime("%Y-%m-%d %H:%M:%S")),
        )


def watchlist() -> list[str]:
    with connect() as con:
        return [r[0] for r in con.execute("SELECT symbol FROM watchlist ORDER BY added_at")]


def save_result(res: dict) -> int:
    with _lock, connect() as con:
        cur = con.execute(
            """INSERT INTO results(practiced_at, trade_date, symbol, pnl, commission, trips, wins, losses, detail)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                now_jst().strftime("%Y-%m-%d %H:%M:%S"),
                res["date"], res["symbol"], res["pnl"], res["commission"],
                res["trips"], res["wins"], res["losses"], json.dumps(res, ensure_ascii=False),
            ),
        )
        return cur.lastrowid


def stats() -> dict:
    with connect() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT id, practiced_at, trade_date, symbol, pnl, commission, trips, wins, losses FROM results ORDER BY id DESC"
        )]
    trips = sum(r["trips"] for r in rows)
    wins = sum(r["wins"] for r in rows)
    days = len(rows)
    win_days = sum(1 for r in rows if r["pnl"] > 0)
    return {
        "days": days,
        "total_pnl": round(sum(r["pnl"] for r in rows), 1),
        "trips": trips,
        "wins": wins,
        "win_rate": round(wins / trips * 100, 1) if trips else None,
        "day_win_rate": round(win_days / days * 100, 1) if days else None,
        "recent": rows[:30],
    }
