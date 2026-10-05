"""サーバーなし版（スマホ用の静的サイト）向けに、1分足を static/data/ へ書き出す。

使い方:
  python export_static.py            # watchlist.json の銘柄を取得して書き出し
  python export_static.py 7203 9984  # 銘柄を watchlist.json に追加してから書き出し
GitHub Actions（.github/workflows/update-data.yml）から平日の大引け後に実行する。

出力:
  static/data/watchlist.json          一括更新リスト
  static/data/index.json              銘柄ごとの練習可能日
  static/data/<銘柄>/<日付>.json      補完済みの1分足（場が終わった日だけ）
  static/data/_refs/<日付>.json       日経平均・ドル円
一度書き出した日は上書きしない（yfinance で取れなくなった古い日もそのまま残る）。
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from dtr import db, market, updater

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("export")

OUT = Path(__file__).resolve().parent / "static" / "data"
REFS = OUT / "_refs"
RANGE_NOTE = "1分足は直近約30日分しか取得できません。毎日の自動更新で練習できる日が蓄積されます。"


def dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def load_watchlist() -> list[str]:
    p = OUT / "watchlist.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def export_symbol(symbol: str) -> int:
    n = 0
    for d in market.selectable_dates(symbol):
        date = d["date"]
        path = OUT / symbol / f"{date}.json"
        if path.exists():
            continue
        bars = market.build_day(symbol, date)
        if not bars:
            continue
        prev = db.prev_close(symbol, date)
        dump(path, {
            "symbol": symbol, "date": date, "prev_close": prev,
            "tick": market.tick_size(prev or bars[0]["o"]),
            # [時刻, 始値, 高値, 安値, 終値, 出来高, 補完足か(1/0)]
            "bars": [[b["t"][-5:], b["o"], b["h"], b["l"], b["c"], b["v"], 1 if b["filled"] else 0] for b in bars],
        })
        ref_path = REFS / f"{date}.json"
        if not ref_path.exists():
            dump(ref_path, {
                k: [[r["t"][-5:], r["c"]] for r in market.ref_bars(sym, date)]
                for k, sym in market.REF_SYMBOLS.items()
            })
        n += 1
    return n


def build_index(watch: list[str]) -> dict:
    symbols = {}
    for d in sorted(p for p in OUT.iterdir() if p.is_dir() and not p.name.startswith("_")):
        dates = sorted((f.stem for f in d.glob("*.json")), reverse=True)
        if dates:
            symbols[d.name] = dates
    return {
        "updated": db.now_jst().strftime("%Y-%m-%d %H:%M"),
        "range_note": RANGE_NOTE,
        "repo": os.environ.get("GITHUB_REPOSITORY", ""),
        "watchlist": watch,
        "symbols": symbols,
    }


def main(argv: list[str]) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    watch = load_watchlist()
    for raw in argv:
        for part in raw.replace(",", " ").split():
            sym = market.normalize_symbol(part)
            if sym not in watch:
                watch.append(sym)
    if not watch:
        watch = ["6857.T"]

    failed = []
    for sym in watch:
        try:
            if updater.update_symbol(sym) == 0:
                failed.append(sym)
        except Exception as e:  # noqa: BLE001
            log.error("%s: 取得失敗 %s", sym, e)
            failed.append(sym)
    updater.update_refs(force=True)

    for sym in watch:
        log.info("%s: %d 日分を新しく書き出し", sym, export_symbol(sym))
    # 1本も取れなかった銘柄（コード間違いなど）で、過去データも無いものはリストから外す
    watch = [s for s in watch if s not in failed or (OUT / s).exists()]
    dump(OUT / "watchlist.json", watch)
    dump(OUT / "index.json", build_index(watch))

    if failed:
        log.warning("取得できなかった銘柄: %s", ", ".join(failed))
    # 全銘柄が失敗したときは異常終了（Yahoo 側に止められた等）にして気づけるようにする
    return 1 if len(failed) == len(set(failed) | set(watch)) and failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
