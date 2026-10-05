"""東証の場時間・呼値・歯抜け補完。"""
from __future__ import annotations

import datetime as dt
import re

from . import db

MORNING = ("09:00", "11:30")
AFTERNOON = ("12:30", "15:30")
CLOSE_TIME = dt.time(15, 30)

REF_SYMBOLS = {"nikkei": "^N225", "usdjpy": "JPY=X"}


def normalize_symbol(raw: str) -> str:
    s = (raw or "").strip().upper()
    if not s:
        raise ValueError("銘柄コードを入力してください")
    # 4桁コード（英字入りの新コード 130A 等も含む）は東証扱い
    if re.fullmatch(r"[0-9][0-9A-Z]{3}", s):
        return s + ".T"
    return s


def session_minutes() -> list[str]:
    """場時間の分ラベル（足の開始時刻）。前場 9:00–11:30、後場 12:30–15:30。"""
    out = []
    for a, b in (MORNING, AFTERNOON):
        t = dt.datetime.strptime(a, "%H:%M")
        end = dt.datetime.strptime(b, "%H:%M")
        while t <= end:
            out.append(t.strftime("%H:%M"))
            t += dt.timedelta(minutes=1)
    return out


def tick_size(price: float) -> float:
    """東証の呼値（TOPIX500以外の標準テーブル）。"""
    table = [
        (3000, 1), (5000, 5), (30000, 10), (50000, 50), (300000, 100),
        (500000, 500), (3000000, 1000), (5000000, 5000), (30000000, 10000), (50000000, 50000),
    ]
    for limit, tick in table:
        if price <= limit:
            return tick
    return 100000


def selectable_dates(symbol: str) -> list[dict]:
    """練習可能日。当日分は大引け(15:30)まで出さない。ザラ場の足が少なすぎる日も除外。"""
    now = db.now_jst()
    today = now.strftime("%Y-%m-%d")
    out = []
    for d in db.stored_dates(symbol):
        if d["date"] > today:
            continue
        if d["date"] == today and now.time() < CLOSE_TIME:
            continue
        if d["bars"] < 30 or d["volume"] <= 0:
            continue
        out.append(d)
    return out


def build_day(symbol: str, date: str) -> list[dict]:
    """場時間グリッドに沿った1分足。取引のない分は直前の終値・出来高0で埋める。

    寄り前に約定がない分（特別気配など）は前日終値で埋める。
    filled=True の足は補完足。
    """
    raw = {b["ts"][11:16]: b for b in db.day_bars(symbol, date, "09:00", "15:30")}
    if not raw:
        return []
    last_close = db.prev_close(symbol, date)
    if last_close is None:
        first = raw[min(raw)]
        last_close = first["open"]
    out = []
    for hm in session_minutes():
        b = raw.get(hm)
        if b is not None and b["volume"] and b["volume"] > 0:
            bar = {
                "t": f"{date} {hm}", "o": b["open"], "h": b["high"], "l": b["low"],
                "c": b["close"], "v": b["volume"], "filled": False,
            }
            last_close = b["close"]
        else:
            bar = {"t": f"{date} {hm}", "o": last_close, "h": last_close, "l": last_close,
                   "c": last_close, "v": 0, "filled": True}
        out.append(bar)
    return out


def ref_bars(symbol: str, date: str) -> list[dict]:
    """参考チャート用（日経平均・ドル円）。指数は出来高0が普通なので volume で補完判定しない。"""
    rows = db.day_bars(symbol, date, "08:00", "15:30")
    return [{"t": r["ts"], "c": r["close"]} for r in rows]
