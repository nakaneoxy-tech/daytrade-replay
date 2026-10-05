"""動作確認：6857 を取得 → 1日分を再生 → 売買 → 15:30 自動決済まで。

  python tests/smoke_test.py
別DB（data/smoke.db）を使うので本番の成績には混ざらない。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ["DTR_DB"] = str(ROOT / "data" / "smoke.db")
sys.path.insert(0, str(ROOT))

from dtr import engine, market, updater  # noqa: E402

res = updater.fetch_and_register("6857")
print("fetched", res)
dates = market.selectable_dates("6857.T")
assert dates, "練習可能日がない"
print("dates", [d["date"] for d in dates])
today = market.db.now_jst().strftime("%Y-%m-%d")
if market.db.now_jst().time() < market.CLOSE_TIME:
    assert today not in [d["date"] for d in dates], "場中に当日が選べてしまう"

date = dates[-1]["date"] if len(dates) > 1 else dates[0]["date"]
s = engine.create_session("6857.T", date, commission=0, slippage_ticks=1)
grid = market.session_minutes()
assert len(s.bars) == len(grid), (len(s.bars), len(grid))
assert [b["t"][-5:] for b in s.bars] == grid, "時刻軸がずれている"
print("bars", len(s.bars), "filled", sum(b["filled"] for b in s.bars))

# 寄り前に買い → 最初の約定足の始値+1tick で約定するはず
s.order("buy", 100)
out = s.step(1)
assert len(out["bars"]) == 1 and out["bars"][0]["t"].endswith("09:00")
first_real = next(b for b in s.bars if not b["filled"])
while s.pos == 0:
    s.step(1)
fill = s.fills[0]
exp = first_real["o"] + market.tick_size(first_real["o"])
assert fill["time"] == first_real["t"] and abs(fill["price"] - exp) < 1e-6, (fill, first_real)
print("entry", fill)

# 未来の足は返らない
assert s.cursor < len(s.bars) - 1
state = s.state()
assert "bars" not in state

# 30分後に売り（ドテン売り 200 株 → 100 株ショート）
s.step(30)
s.order("sell", 200)
s.step(1)
assert s.pos == -100, s.pos
print("flip", s.fills[-1])

# 最後まで進める → 自動決済
s.step(400)
assert s.finished and s.pos == 0
print("final fill", s.fills[-1])
print("result", {k: s.result[k] for k in ("pnl", "trips", "wins", "losses", "win_rate")})
assert s.result["trips"] == 2

# 損益の検算（手数料0なので約定ごとの実現損益の合計＝日次損益）
pnl = sum(f["realized"] for f in s.fills)
assert abs(pnl - s.result["pnl"]) < 1, (pnl, s.result["pnl"])
print("OK")
