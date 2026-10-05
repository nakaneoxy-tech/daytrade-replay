"""J-Quants API（分足アドオン）用プロバイダのスタブ。

実装時の想定:
  - 環境変数 JQUANTS_REFRESH_TOKEN（または メール/パスワード）で ID トークンを取得
  - 分足エンドポイントから指定銘柄・期間の1分足を取得
  - DataProvider.fetch_1m と同じ列形式（ts=JST 'YYYY-MM-DD HH:MM'）に変換して返す
J-Quants は過去数年分を取得できるため、range_note もそれに合わせて変更する。
"""
from __future__ import annotations

import os

import pandas as pd

from .base import DataProvider


class JQuantsProvider(DataProvider):
    name = "jquants"
    range_note = "J-Quants（分足アドオン契約が必要）"

    def __init__(self) -> None:
        self.refresh_token = os.environ.get("JQUANTS_REFRESH_TOKEN")

    def fetch_1m(self, symbol: str) -> pd.DataFrame:
        raise NotImplementedError("J-Quants プロバイダは未実装です（スタブ）。DATA_PROVIDER=yfinance を使ってください。")
