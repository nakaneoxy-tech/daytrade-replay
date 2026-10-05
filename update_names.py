"""銘柄コード→銘柄名の一覧（static/data/names.json）を、東証（JPX）の上場銘柄一覧から作る。

  python update_names.py
画面でコードを入れたときに銘柄名を出すために使う。取得に失敗したときは今ある names.json をそのまま残す。
"""
from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

import pandas as pd
import requests

PAGE = "https://www.jpx.co.jp/markets/statistics-equities/misc/01.html"
OUT = Path(__file__).resolve().parent / "static" / "data" / "names.json"
UA = {"User-Agent": "Mozilla/5.0"}


def fetch_table() -> pd.DataFrame:
    """JPX の上場銘柄一覧（コード・銘柄名・市場・商品区分など）。"""
    html = requests.get(PAGE, timeout=30, headers=UA).text
    m = re.search(r'href="([^"]+data_j\.xlsx?)"', html)
    if not m:
        raise RuntimeError("JPX のページに一覧ファイルのリンクが見つかりません")
    r = requests.get("https://www.jpx.co.jp" + m.group(1), timeout=60, headers=UA)
    r.raise_for_status()
    return pd.read_excel(io.BytesIO(r.content), dtype=str)


def fetch_names() -> dict[str, str]:
    df = fetch_table()
    names = {}
    for code, name in zip(df["コード"], df["銘柄名"]):
        code, name = str(code).strip().upper(), str(name).strip()
        if re.fullmatch(r"[0-9][0-9A-Z]{3}", code) and name:
            names[code] = name
    if len(names) < 1000:
        raise RuntimeError(f"銘柄数が少なすぎます（{len(names)}件）")
    return names


if __name__ == "__main__":
    try:
        names = fetch_names()
    except Exception as e:  # noqa: BLE001 - 失敗しても既存の一覧で動き続ける
        print(f"銘柄名の更新に失敗（既存の一覧を使います）: {e}", file=sys.stderr)
        sys.exit(0)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(names, ensure_ascii=False, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    print(f"{len(names)} 銘柄を書き出しました")
