"""データ取得・更新。"""
from __future__ import annotations

import datetime as dt
import logging

from . import db, market
from .providers import get_provider

log = logging.getLogger(__name__)

REF_FRESH_MINUTES = 30


def update_symbol(symbol: str) -> int:
    provider = get_provider()
    df = provider.fetch_1m(symbol)
    n = db.upsert_bars(symbol, df)
    log.info("%s: %d bars", symbol, n)
    return n


def update_refs(force: bool = False) -> dict[str, int]:
    out = {}
    for sym in market.REF_SYMBOLS.values():
        lu = db.last_updated(sym)
        if not force and lu and db.now_jst() - lu < dt.timedelta(minutes=REF_FRESH_MINUTES):
            out[sym] = 0
            continue
        out[sym] = update_symbol(sym)
    return out


def fetch_and_register(raw_symbol: str, force_refs: bool = False) -> dict:
    """任意銘柄を取得し、一括更新リストに登録する。"""
    symbol = market.normalize_symbol(raw_symbol)
    n = update_symbol(symbol)
    if n == 0 and not db.stored_dates(symbol):
        raise ValueError(f"{symbol} の1分足を取得できませんでした（コードを確認してください）")
    db.add_watch(symbol)
    refs = update_refs(force_refs)
    return {"symbol": symbol, "fetched": n, "refs": refs}


def update_all() -> dict[str, int]:
    """一括更新リスト＋参考指標（日経平均・ドル円）を更新。"""
    out = {}
    for sym in db.watchlist():
        try:
            out[sym] = update_symbol(sym)
        except Exception as e:  # noqa: BLE001
            log.error("update %s failed: %s", sym, e)
            out[sym] = -1
    out.update(update_refs(force=True))
    return out
