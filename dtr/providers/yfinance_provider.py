"""yfinance による1分足取得。

yfinance の1分足は「1リクエスト最大8日・遡れるのは直近30日程度」の制約がある。
そのため7日ずつ区切って直近29日分を取得する。
"""
from __future__ import annotations

import datetime as dt
import logging

import pandas as pd
import yfinance as yf

from .base import DataProvider

log = logging.getLogger(__name__)


class YFinanceProvider(DataProvider):
    name = "yfinance"
    range_note = "yfinance の1分足は直近約30日分しか取得できません。定期的に「データ更新」すると練習できる日が蓄積されます。"

    LOOKBACK_DAYS = 29
    CHUNK_DAYS = 7

    def fetch_1m(self, symbol: str) -> pd.DataFrame:
        now = dt.datetime.now(dt.timezone.utc)
        start = now - dt.timedelta(days=self.LOOKBACK_DAYS)
        frames = []
        t = yf.Ticker(symbol)
        cur = start
        while cur < now:
            end = min(cur + dt.timedelta(days=self.CHUNK_DAYS), now + dt.timedelta(minutes=1))
            try:
                df = t.history(start=cur, end=end, interval="1m", prepost=False, auto_adjust=False)
            except Exception as e:  # noqa: BLE001 - 取得失敗は区間単位でスキップ
                log.warning("yfinance %s %s-%s failed: %s", symbol, cur, end, e)
                df = None
            if df is not None and len(df):
                frames.append(df)
            cur = end
        if not frames:
            return self.empty()
        df = pd.concat(frames)
        df = df[~df.index.duplicated(keep="last")]
        idx = df.index.tz_convert("Asia/Tokyo")
        out = pd.DataFrame(
            {
                "ts": idx.strftime("%Y-%m-%d %H:%M"),
                "open": df["Open"].astype(float).values,
                "high": df["High"].astype(float).values,
                "low": df["Low"].astype(float).values,
                "close": df["Close"].astype(float).values,
                "volume": df["Volume"].fillna(0).astype(float).values,
            }
        )
        out = out.dropna(subset=["open", "high", "low", "close"])
        return out.reset_index(drop=True)
