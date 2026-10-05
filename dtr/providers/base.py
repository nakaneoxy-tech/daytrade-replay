"""DataProvider インターフェース。取得元を差し替えるときはこれを実装する。"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class DataProvider(ABC):
    """1分足を返すプロバイダ。

    fetch_1m の戻り値:
      index なし、列 = ts(str 'YYYY-MM-DD HH:MM', JST), open, high, low, close, volume
    """

    name: str = "base"
    # UIに表示する「取得できる期間」の説明
    range_note: str = ""

    @abstractmethod
    def fetch_1m(self, symbol: str) -> pd.DataFrame:
        """取得可能な範囲の1分足をまとめて返す。"""
        raise NotImplementedError

    @staticmethod
    def empty() -> pd.DataFrame:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
