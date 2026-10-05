import os

from .base import DataProvider


def get_provider(name: str | None = None) -> DataProvider:
    """環境変数 DATA_PROVIDER（既定 yfinance）でプロバイダを選ぶ。"""
    name = (name or os.environ.get("DATA_PROVIDER") or "yfinance").lower()
    if name == "yfinance":
        from .yfinance_provider import YFinanceProvider

        return YFinanceProvider()
    if name == "jquants":
        from .jquants_provider import JQuantsProvider

        return JQuantsProvider()
    raise ValueError(f"unknown provider: {name}")
