"""リプレイ＆仮想売買エンジン。

未来の足をクライアントに渡さないため、セッションはサーバー側で保持し、
step() で進めた分の足だけを返す。注文は「次の（約定のある）1分足の始値」で約定する。
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field

from . import db, market


@dataclass
class Trip:
    """フラット→建玉→フラットまでの1往復。"""
    side: str
    entry_time: str
    entry_price: float
    qty: int = 0
    pnl: float = 0.0          # 手数料込みの実現損益
    exit_time: str | None = None
    exit_price: float | None = None


@dataclass
class Session:
    symbol: str
    date: str
    bars: list[dict]
    refs: dict[str, list[dict]]
    commission: float = 0.0       # 1約定あたり円
    slippage_ticks: int = 0
    prev_close: float | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    cursor: int = -1              # 公開済みの最後の足 index（-1 = 寄り前）
    pos: int = 0
    avg: float = 0.0
    realized: float = 0.0
    commission_total: float = 0.0
    pending: list[dict] = field(default_factory=list)
    fills: list[dict] = field(default_factory=list)
    trips: list[Trip] = field(default_factory=list)
    open_trip: Trip | None = None
    finished: bool = False
    result: dict | None = None
    _ref_sent: dict[str, int] = field(default_factory=dict)
    _order_seq: int = 0

    # ---- 公開状態 -------------------------------------------------------
    @property
    def now_label(self) -> str:
        if self.cursor < 0:
            return f"{self.date} 寄り前"
        return self.bars[self.cursor]["t"]

    def last_price(self) -> float | None:
        if self.cursor < 0:
            return self.prev_close
        return self.bars[self.cursor]["c"]

    def unrealized(self) -> float:
        p = self.last_price()
        if not self.pos or p is None:
            return 0.0
        return (p - self.avg) * self.pos

    def state(self) -> dict:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "date": self.date,
            "cursor": self.cursor,
            "total": len(self.bars),
            "now": self.now_label,
            "pos": self.pos,
            "avg": round(self.avg, 2),
            "last": self.last_price(),
            "realized": round(self.realized, 1),
            "unrealized": round(self.unrealized(), 1),
            "commission_total": round(self.commission_total, 1),
            "pending": list(self.pending),
            "finished": self.finished,
            "result": self.result,
            "settings": {"commission": self.commission, "slippage_ticks": self.slippage_ticks},
        }

    # ---- 注文 -----------------------------------------------------------
    def order(self, side: str, qty: int) -> dict:
        if self.finished:
            raise ValueError("この日の練習は終了しています")
        if side not in ("buy", "sell"):
            raise ValueError("side は buy / sell")
        if qty <= 0 or qty % 100:
            raise ValueError("株数は100株単位で指定してください")
        self._order_seq += 1
        o = {"id": self._order_seq, "side": side, "qty": qty,
             "placed": self.bars[self.cursor]["t"][-5:] if self.cursor >= 0 else "寄り前"}
        self.pending.append(o)
        return o

    def flatten(self) -> dict | None:
        """全決済：保留中の新規注文を取り消し、建玉を反対売買する（次の足の始値）。"""
        self.pending.clear()
        if self.pos == 0:
            return None
        return self.order("sell" if self.pos > 0 else "buy", abs(self.pos))

    def cancel_all(self) -> None:
        self.pending.clear()

    # ---- 進行 -----------------------------------------------------------
    def step(self, n: int = 1) -> dict:
        new_bars, new_fills = [], []
        start_cursor = self.cursor
        for _ in range(max(1, n)):
            if self.finished or self.cursor + 1 >= len(self.bars):
                break
            self.cursor += 1
            bar = self.bars[self.cursor]
            # 約定のある足の始値でのみ約定（補完足では約定させない）
            if self.pending and not bar["filled"]:
                for o in self.pending:
                    new_fills.append(self._fill(o, bar))
                self.pending.clear()
            new_bars.append(bar)
            if self.cursor == len(self.bars) - 1:
                new_fills.extend(self._finish())
        refs = {k: self._ref_until(k) for k in self.refs}
        return {
            "bars": [{k: b[k] for k in ("t", "o", "h", "l", "c", "v", "filled")} for b in new_bars],
            "from": start_cursor + 1,
            "fills": new_fills,
            "refs": refs,
            "state": self.state(),
        }

    def _ref_until(self, key: str) -> list[dict]:
        rows = self.refs[key]
        cut = self.bars[self.cursor]["t"] if self.cursor >= 0 else f"{self.date} 09:00"
        sent = self._ref_sent.get(key, 0)
        i = sent
        # 現在の足の時刻 "以前" だけ（cursor<0 のときは 9:00 より前のみ）
        while i < len(rows) and (rows[i]["t"] <= cut if self.cursor >= 0 else rows[i]["t"] < cut):
            i += 1
        self._ref_sent[key] = i
        return rows[sent:i]

    def _price_with_slip(self, side: str, price: float) -> float:
        if not self.slippage_ticks:
            return price
        t = market.tick_size(price) * self.slippage_ticks
        return price + t if side == "buy" else price - t

    def _apply(self, side: str, qty: int, price: float, time: str, reason: str) -> dict:
        signed = qty if side == "buy" else -qty
        realized = 0.0
        fee = self.commission
        self.commission_total += fee
        prev_pos = self.pos

        if self.pos == 0 or (self.pos > 0) == (signed > 0):
            new_abs = abs(self.pos) + qty
            self.avg = (self.avg * abs(self.pos) + price * qty) / new_abs
            self.pos += signed
        else:
            closing = min(qty, abs(self.pos))
            realized = closing * (price - self.avg) * (1 if self.pos > 0 else -1)
            self.pos += signed
            if self.pos == 0:
                self.avg = 0.0
            elif (self.pos > 0) != (prev_pos > 0):
                self.avg = price  # ドテン：残りは新規建て
        self.realized += realized - fee

        # 往復トレードの集計
        if prev_pos == 0 and self.pos != 0:
            self.open_trip = Trip("long" if self.pos > 0 else "short", time, price)
        if self.open_trip is not None:
            self.open_trip.pnl += realized - fee
            self.open_trip.qty = max(self.open_trip.qty, abs(self.pos), abs(prev_pos))
            crossed = prev_pos != 0 and (self.pos == 0 or (self.pos > 0) != (prev_pos > 0))
            if crossed:
                self.open_trip.exit_time, self.open_trip.exit_price = time, price
                self.trips.append(self.open_trip)
                self.open_trip = None
                if self.pos != 0:
                    self.open_trip = Trip("long" if self.pos > 0 else "short", time, price, qty=abs(self.pos))

        f = {
            "time": time, "side": side, "qty": qty, "price": round(price, 2),
            "realized": round(realized, 1), "fee": fee, "pos_after": self.pos, "reason": reason,
        }
        self.fills.append(f)
        return f

    def _fill(self, o: dict, bar: dict) -> dict:
        price = self._price_with_slip(o["side"], bar["o"])
        return self._apply(o["side"], o["qty"], price, bar["t"], f"注文#{o['id']}（{o['placed']}発注）→ 始値約定")

    def _finish(self) -> list[dict]:
        """15:30到達：未約定注文は取消、残ポジションを最終足の終値で自動決済。"""
        out = []
        self.pending.clear()
        last = self.bars[-1]
        if self.pos:
            side = "sell" if self.pos > 0 else "buy"
            price = self._price_with_slip(side, last["c"])
            out.append(self._apply(side, abs(self.pos), price, last["t"], "15:30 自動決済（大引け）"))
        self.finished = True
        wins = sum(1 for t in self.trips if t.pnl > 0)
        losses = sum(1 for t in self.trips if t.pnl <= 0)
        self.result = {
            "symbol": self.symbol,
            "date": self.date,
            "pnl": round(self.realized, 1),
            "commission": round(self.commission_total, 1),
            "trips": len(self.trips),
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / len(self.trips) * 100, 1) if self.trips else None,
            "fills": self.fills,
            "trip_list": [t.__dict__ for t in self.trips],
            "settings": {"commission": self.commission, "slippage_ticks": self.slippage_ticks},
        }
        self.result["result_id"] = db.save_result(self.result)
        return out


_sessions: dict[str, Session] = {}
_slock = threading.Lock()


def create_session(symbol: str, date: str, commission: float = 0.0, slippage_ticks: int = 0) -> Session:
    if date not in {d["date"] for d in market.selectable_dates(symbol)}:
        raise ValueError(f"{symbol} の {date} は練習日として選べません")
    bars = market.build_day(symbol, date)
    refs = {k: market.ref_bars(sym, date) for k, sym in market.REF_SYMBOLS.items()}
    s = Session(
        symbol=symbol, date=date, bars=bars, refs=refs,
        commission=float(commission or 0), slippage_ticks=int(slippage_ticks or 0),
        prev_close=db.prev_close(symbol, date),
    )
    with _slock:
        # 古いセッションは捨てる（メモリ保持のみ）
        if len(_sessions) > 20:
            for k in list(_sessions)[:-10]:
                _sessions.pop(k, None)
        _sessions[s.id] = s
    return s


def get_session(sid: str) -> Session:
    s = _sessions.get(sid)
    if s is None:
        raise KeyError("セッションが見つかりません（サーバー再起動後は再開始してください）")
    return s
