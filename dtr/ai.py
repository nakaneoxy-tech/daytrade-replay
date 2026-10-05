"""AIトレード分析（任意機能）。ANTHROPIC_API_KEY が未設定なら無効。従量課金。"""
from __future__ import annotations

import os

from .engine import Session

MODEL = os.environ.get("DTR_AI_MODEL", "claude-opus-5")


def enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _summarize(s: Session) -> str:
    bars = [b for b in s.bars if not b["filled"]]
    lines = [f"銘柄: {s.symbol}  日付: {s.date}  前日終値: {s.prev_close}"]
    if bars:
        hi = max(bars, key=lambda b: b["h"])
        lo = min(bars, key=lambda b: b["l"])
        lines.append(
            f"始値 {bars[0]['o']} / 高値 {hi['h']}({hi['t'][-5:]}) / 安値 {lo['l']}({lo['t'][-5:]}) / 終値 {bars[-1]['c']}"
        )
    # 15分ごとの値動き要約（VWAP付き）
    pv = vol = 0.0
    lines.append("時刻 | 15分足 始-高-安-終 | 出来高 | VWAP")
    chunk: list[dict] = []
    for b in s.bars:
        if b["v"]:
            pv += (b["h"] + b["l"] + b["c"]) / 3 * b["v"]
            vol += b["v"]
        chunk.append(b)
        if len(chunk) == 15 or b is s.bars[-1] or b["t"].endswith("11:30"):
            vw = pv / vol if vol else None
            lines.append(
                f"{chunk[0]['t'][-5:]}-{chunk[-1]['t'][-5:]} | {chunk[0]['o']}-{max(c['h'] for c in chunk)}-"
                f"{min(c['l'] for c in chunk)}-{chunk[-1]['c']} | {int(sum(c['v'] for c in chunk))} | "
                f"{round(vw, 1) if vw else '-'}"
            )
            chunk = []
    lines.append("\n売買履歴（約定は次の1分足の始値）:")
    for f in s.fills:
        lines.append(
            f"{f['time'][-5:]} {'買' if f['side']=='buy' else '売'} {f['qty']}株 @{f['price']} 実現{f['realized']} 建玉後{f['pos_after']} ({f['reason']})"
        )
    r = s.result or {}
    lines.append(
        f"\n結果: 損益 {r.get('pnl')}円 / 往復 {r.get('trips')}回 / 勝ち {r.get('wins')} 負け {r.get('losses')} / 手数料 {r.get('commission')}円"
    )
    return "\n".join(lines)


SYSTEM = (
    "あなたは日本株デイトレードのコーチです。過去チャートのリプレイ練習の結果を講評します。"
    "このトレーダーの典型的な負けパターンは『急騰中の飛びつき買い（高値掴み）』と『根拠の薄い適当なエントリー』です。"
    "値動きの要約と売買履歴だけを根拠に、(1)良かった判断（最低1つ）、(2)改善点（具体的な時刻・価格・VWAPとの位置関係を挙げる）、"
    "(3)次回の練習で試すルール1〜2個、を日本語で簡潔に書いてください。慰めは不要、数値は小数点1位まで。"
)


def analyze(s: Session) -> str:
    if not enabled():
        raise RuntimeError("ANTHROPIC_API_KEY が未設定のためAI分析は無効です")
    if not s.finished:
        raise RuntimeError("1日の練習が終わってから分析できます")
    import anthropic

    client = anthropic.Anthropic()
    resp = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": _summarize(s)}],
        # 安全分類器で拒否された場合はサーバー側で別モデルにフォールバック
        betas=["server-side-fallback-2026-07-01"],
        extra_body={"fallbacks": "default", "output_config": {"effort": "medium"}},
    )
    if resp.stop_reason == "refusal":
        return "（AIが回答を拒否しました）"
    return "".join(b.text for b in resp.content if b.type == "text")
