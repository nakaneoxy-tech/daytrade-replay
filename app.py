"""デイトレ練習アプリ（過去1分足リプレイ）— Flask サーバー

起動: python app.py  →  http://localhost:8765
"""
from __future__ import annotations

import logging
import os

from flask import Flask, jsonify, request, send_from_directory

from dtr import ai, db, engine, market, updater
from dtr.providers import get_provider

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
# 静的ファイルはルート直下で配信（スマホ用の静的サイトと同じ相対パスで動くようにする）
app = Flask(__name__, static_folder="static", static_url_path="")


def err(msg: str, code: int = 400):
    return jsonify({"error": msg}), code


@app.errorhandler(ValueError)
def _value_error(e):
    return err(str(e))


@app.errorhandler(KeyError)
def _key_error(e):
    return err(str(e.args[0]) if e.args else "not found", 404)


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.get("/api/config")
def config():
    p = get_provider()
    return jsonify({
        "provider": p.name,
        "range_note": p.range_note,
        "ai_enabled": ai.enabled(),
        "ai_model": ai.MODEL,
        "watchlist": db.watchlist(),
    })


@app.post("/api/fetch")
def fetch():
    sym = (request.json or {}).get("symbol", "")
    res = updater.fetch_and_register(sym)
    res["dates"] = market.selectable_dates(res["symbol"])
    return jsonify(res)


@app.post("/api/update")
def update():
    """データ更新ボタン。symbol 指定時はその銘柄＋参考指標、未指定なら一括更新。"""
    sym = (request.json or {}).get("symbol")
    if sym:
        res = updater.fetch_and_register(sym, force_refs=True)
        res["dates"] = market.selectable_dates(res["symbol"])
        return jsonify(res)
    return jsonify({"updated": updater.update_all()})


@app.get("/api/dates")
def dates():
    sym = market.normalize_symbol(request.args.get("symbol", ""))
    return jsonify({"symbol": sym, "dates": market.selectable_dates(sym)})


@app.post("/api/session")
def new_session():
    j = request.json or {}
    sym = market.normalize_symbol(j.get("symbol", ""))
    s = engine.create_session(sym, j.get("date", ""), j.get("commission", 0), j.get("slippage_ticks", 0))
    # 寄り前状態で開始（足は未公開）。参考チャートは 9:00 より前の分だけ返す
    return jsonify({
        "state": s.state(),
        "prev_close": s.prev_close,
        "tick": market.tick_size(s.prev_close or s.bars[0]["o"]),
        "refs": {k: s._ref_until(k) for k in s.refs},
    })


@app.post("/api/session/<sid>/step")
def step(sid):
    n = int((request.json or {}).get("n", 1))
    return jsonify(engine.get_session(sid).step(min(max(n, 1), 400)))


@app.post("/api/session/<sid>/order")
def order(sid):
    j = request.json or {}
    s = engine.get_session(sid)
    action = j.get("action")
    if action == "flatten":
        o = s.flatten()
    elif action == "cancel":
        s.cancel_all()
        o = None
    else:
        o = s.order(action, int(j.get("qty", 100)))
    return jsonify({"order": o, "state": s.state()})


@app.post("/api/session/<sid>/ai")
def ai_analyze(sid):
    s = engine.get_session(sid)
    try:
        return jsonify({"text": ai.analyze(s)})
    except RuntimeError as e:
        return err(str(e))
    except Exception as e:  # noqa: BLE001 - API エラーはそのまま表示
        return err(f"AI分析に失敗しました: {e}", 502)


@app.get("/api/stats")
def stats():
    return jsonify(db.stats())


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8765))
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
