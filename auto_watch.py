"""売買代金ランキング上位の個別株を、毎日の自動更新の対象に入れる。

  static/data/auto.json  … {"銘柄": "最後にランキング上位に入った日"}
手動で追加した銘柄（watchlist.json）はずっと更新し続ける。自動で入った銘柄は、
ランキングから KEEP_DAYS 日外れたら更新をやめる（書き出し済みの日は消さない）。
1分足は約30日さかのぼって取れるので、また上位に戻れば抜けていた日も埋まる。

設定（環境変数）:
  DTR_AUTO_TOP_N      上位何銘柄を入れるか（既定 10。0 で自動登録なし）
  DTR_AUTO_KEEP_DAYS  ランキングから外れて何日で更新をやめるか（既定 7）
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
from pathlib import Path

import requests

from dtr import db

log = logging.getLogger("auto_watch")

OUT = Path(__file__).resolve().parent / "static" / "data" / "auto.json"
TOP_N = int(os.environ.get("DTR_AUTO_TOP_N", "10"))
KEEP_DAYS = int(os.environ.get("DTR_AUTO_KEEP_DAYS", "7"))
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    "Accept-Language": "ja",
}
CODE = r"[0-9][0-9A-Z]{3}"


def _yahoo() -> list[str]:
    url = "https://finance.yahoo.co.jp/stocks/ranking/tradingValueHigh?market=all&term=daily"
    html = requests.get(url, headers=UA, timeout=30).text
    m = re.search(r"window\.__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*</script>", html, re.S)
    rows = json.loads(m.group(1))["mainRankingList"]["results"]
    rows.sort(key=lambda r: int(r["rank"]))
    return [r["stockCode"].upper() for r in rows if re.fullmatch(CODE, r["stockCode"].upper())]


def _kabutan() -> list[str]:
    html = requests.get("https://kabutan.jp/warning/trading_value_ranking", headers=UA, timeout=30).text
    out = []
    for c in re.findall(rf"/stock/\?code=({CODE})", html):
        # 0000 などは指数へのリンク
        if not c.startswith("0") and c not in out:
            out.append(c)
    return out


def _stock_codes() -> set[str] | None:
    """ETFなどを除いた個別株のコード（JPXの上場銘柄一覧から）。取れなければ None。"""
    try:
        from update_names import fetch_table

        df = fetch_table()
        return {str(c).strip().upper() for c, k in zip(df["コード"], df["市場・商品区分"]) if "内国株式" in str(k)}
    except Exception as e:  # noqa: BLE001
        log.warning("個別株の一覧を取得できません（コード帯でETFを除外します）: %s", e)
        return None


def _looks_like_etf(code: str) -> bool:
    return code.isdigit() and (1300 <= int(code) <= 1699 or 2500 <= int(code) <= 2699)


def ranking(top_n: int = TOP_N) -> list[str]:
    codes: list[str] = []
    for name, fn in (("Yahoo!ファイナンス", _yahoo), ("株探", _kabutan)):
        try:
            codes = fn()
            if len(codes) >= top_n:
                log.info("売買代金ランキング: %s から %d 銘柄", name, len(codes))
                break
        except Exception as e:  # noqa: BLE001
            log.warning("%s のランキング取得に失敗: %s", name, e)
    stocks = _stock_codes()
    codes = [c for c in codes if (c in stocks if stocks else not _looks_like_etf(c))]
    return [c + ".T" for c in codes[:top_n]]


def load() -> dict[str, str]:
    return json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}


def refresh() -> list[str]:
    """ランキングを反映し、いま更新対象になっている自動登録銘柄を返す。"""
    if TOP_N <= 0:
        return []
    auto = load()
    today = db.now_jst().date()
    for sym in ranking():
        auto[sym] = today.isoformat()
    limit = (today - dt.timedelta(days=KEEP_DAYS)).isoformat()
    auto = {s: d for s, d in auto.items() if d >= limit}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(auto, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
    return sorted(auto)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(ranking())
