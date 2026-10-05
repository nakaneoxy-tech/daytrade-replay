// サーバーなし版（スマホ用の静的サイト）のバックエンド。
// dtr/engine.py と同じルールをブラウザ内で再現する：注文は次の（約定のある）1分足の始値で約定、15:30 に自動決済。
// 1日分のデータは data/<銘柄>/<日付>.json から読むが、画面（app.js）には進めた分の足しか渡さない。
(() => {
  const RESULTS_KEY = 'dtr.results';
  let index = null;
  const sessions = new Map();

  async function getJSON(url) {
    const r = await fetch(url, { cache: 'no-cache' });
    if (!r.ok) throw new Error(`${url} を読み込めません（HTTP ${r.status}）`);
    return r.json();
  }
  const loadIndex = async () => (index ||= await getJSON('data/index.json'));

  function normalize(raw) {
    const s = String(raw || '').trim().toUpperCase();
    if (!s) throw new Error('銘柄コードを入力してください');
    return /^[0-9][0-9A-Z]{3}$/.test(s) ? s + '.T' : s;
  }
  // 東証の呼値（標準テーブル）。dtr/market.py の tick_size と同じ
  function tickSize(p) {
    const table = [[3000, 1], [5000, 5], [30000, 10], [50000, 50], [300000, 100], [500000, 500],
      [3000000, 1000], [5000000, 5000], [30000000, 10000], [50000000, 50000]];
    for (const [limit, tick] of table) if (p <= limit) return tick;
    return 100000;
  }
  const r1 = (v) => Math.round(v * 10) / 10;
  const r2 = (v) => Math.round(v * 100) / 100;

  function loadResults() {
    try { return JSON.parse(localStorage.getItem(RESULTS_KEY) || '[]'); } catch { return []; }
  }
  function saveResult(res) {
    const all = loadResults();
    all.push(res);
    try { localStorage.setItem(RESULTS_KEY, JSON.stringify(all)); } catch { /* 保存できない端末でも練習は続けられる */ }
    return all.length;
  }

  class Session {
    constructor(day, refs, commission, slippageTicks) {
      this.id = Math.random().toString(36).slice(2, 14);
      this.symbol = day.symbol; this.date = day.date; this.prevClose = day.prev_close;
      this.bars = day.bars.map(([hm, o, h, l, c, v, f]) => ({ t: `${day.date} ${hm}`, o, h, l, c, v, filled: !!f }));
      this.refs = {};
      for (const k of ['nikkei', 'usdjpy']) this.refs[k] = (refs[k] || []).map(([hm, c]) => ({ t: `${day.date} ${hm}`, c }));
      this.refSent = { nikkei: 0, usdjpy: 0 };
      this.commission = Number(commission) || 0;
      this.slippageTicks = Math.trunc(Number(slippageTicks) || 0);
      this.cursor = -1; this.pos = 0; this.avg = 0; this.realized = 0; this.commissionTotal = 0;
      this.pending = []; this.fills = []; this.trips = []; this.openTrip = null;
      this.finished = false; this.result = null; this.orderSeq = 0;
    }
    get nowLabel() { return this.cursor < 0 ? `${this.date} 寄り前` : this.bars[this.cursor].t; }
    lastPrice() { return this.cursor < 0 ? this.prevClose : this.bars[this.cursor].c; }
    unrealized() { const p = this.lastPrice(); return !this.pos || p == null ? 0 : (p - this.avg) * this.pos; }
    state() {
      return {
        id: this.id, symbol: this.symbol, date: this.date, cursor: this.cursor, total: this.bars.length,
        now: this.nowLabel, pos: this.pos, avg: r2(this.avg), last: this.lastPrice(),
        realized: r1(this.realized), unrealized: r1(this.unrealized()), commission_total: r1(this.commissionTotal),
        pending: this.pending.slice(), finished: this.finished, result: this.result,
        settings: { commission: this.commission, slippage_ticks: this.slippageTicks },
      };
    }
    order(side, qty) {
      if (this.finished) throw new Error('この日の練習は終了しています');
      if (side !== 'buy' && side !== 'sell') throw new Error('side は buy / sell');
      if (!(qty > 0) || qty % 100) throw new Error('株数は100株単位で指定してください');
      const o = { id: ++this.orderSeq, side, qty, placed: this.cursor >= 0 ? this.bars[this.cursor].t.slice(-5) : '寄り前' };
      this.pending.push(o);
      return o;
    }
    flatten() {
      this.pending = [];
      if (this.pos === 0) return null;
      return this.order(this.pos > 0 ? 'sell' : 'buy', Math.abs(this.pos));
    }
    step(n) {
      const newBars = [], newFills = [];
      const from = this.cursor + 1;
      for (let i = 0; i < Math.max(1, n); i++) {
        if (this.finished || this.cursor + 1 >= this.bars.length) break;
        const bar = this.bars[++this.cursor];
        // 約定のある足の始値でのみ約定（補完足では約定させない）
        if (this.pending.length && !bar.filled) {
          for (const o of this.pending) {
            newFills.push(this.apply(o.side, o.qty, this.withSlip(o.side, bar.o), bar.t, `注文#${o.id}（${o.placed}発注）→ 始値約定`));
          }
          this.pending = [];
        }
        newBars.push(bar);
        if (this.cursor === this.bars.length - 1) newFills.push(...this.finish());
      }
      return { bars: newBars.map((b) => ({ ...b })), from, fills: newFills, refs: this.refsUntil(), state: this.state() };
    }
    refsUntil() {
      const out = {};
      const cut = this.cursor >= 0 ? this.bars[this.cursor].t : `${this.date} 09:00`;
      for (const k of Object.keys(this.refs)) {
        const rows = this.refs[k];
        let i = this.refSent[k];
        const start = i;
        while (i < rows.length && (this.cursor >= 0 ? rows[i].t <= cut : rows[i].t < cut)) i++;
        this.refSent[k] = i;
        out[k] = rows.slice(start, i);
      }
      return out;
    }
    withSlip(side, price) {
      if (!this.slippageTicks) return price;
      const t = tickSize(price) * this.slippageTicks;
      return side === 'buy' ? price + t : price - t;
    }
    apply(side, qty, price, time, reason) {
      const signed = side === 'buy' ? qty : -qty;
      const fee = this.commission;
      const prev = this.pos;
      let realized = 0;
      this.commissionTotal += fee;
      if (this.pos === 0 || (this.pos > 0) === (signed > 0)) {
        this.avg = (this.avg * Math.abs(this.pos) + price * qty) / (Math.abs(this.pos) + qty);
        this.pos += signed;
      } else {
        const closing = Math.min(qty, Math.abs(this.pos));
        realized = closing * (price - this.avg) * (this.pos > 0 ? 1 : -1);
        this.pos += signed;
        if (this.pos === 0) this.avg = 0;
        else if ((this.pos > 0) !== (prev > 0)) this.avg = price; // ドテン：残りは新規建て
      }
      this.realized += realized - fee;

      // 往復トレードの集計（建玉ゼロ→建つ→ゼロに戻る／ドテンするまでが1往復）
      const newTrip = (q) => ({ side: this.pos > 0 ? 'long' : 'short', entry_time: time, entry_price: price, qty: q, pnl: 0, exit_time: null, exit_price: null });
      if (prev === 0 && this.pos !== 0) this.openTrip = newTrip(0);
      if (this.openTrip) {
        this.openTrip.pnl += realized - fee;
        this.openTrip.qty = Math.max(this.openTrip.qty, Math.abs(this.pos), Math.abs(prev));
        if (prev !== 0 && (this.pos === 0 || (this.pos > 0) !== (prev > 0))) {
          this.openTrip.exit_time = time; this.openTrip.exit_price = price;
          this.trips.push(this.openTrip);
          this.openTrip = this.pos !== 0 ? newTrip(Math.abs(this.pos)) : null;
        }
      }
      const f = { time, side, qty, price: r2(price), realized: r1(realized), fee, pos_after: this.pos, reason };
      this.fills.push(f);
      return f;
    }
    finish() {
      // 15:30到達：未約定注文は取消、残ポジションを最終足の終値で自動決済
      const out = [];
      this.pending = [];
      const last = this.bars[this.bars.length - 1];
      if (this.pos) {
        const side = this.pos > 0 ? 'sell' : 'buy';
        out.push(this.apply(side, Math.abs(this.pos), this.withSlip(side, last.c), last.t, '15:30 自動決済（大引け）'));
      }
      this.finished = true;
      const wins = this.trips.filter((t) => t.pnl > 0).length;
      this.result = {
        symbol: this.symbol, date: this.date, pnl: r1(this.realized), commission: r1(this.commissionTotal),
        trips: this.trips.length, wins, losses: this.trips.length - wins,
        win_rate: this.trips.length ? r1(wins / this.trips.length * 100) : null,
        fills: this.fills, trip_list: this.trips,
        settings: { commission: this.commission, slippage_ticks: this.slippageTicks },
        practiced_at: new Date().toISOString(),
      };
      this.result.result_id = saveResult(this.result);
      return out;
    }
  }

  function stats() {
    const rows = loadResults().map((r, i) => ({
      id: i + 1, practiced_at: r.practiced_at, trade_date: r.date, symbol: r.symbol,
      pnl: r.pnl, commission: r.commission, trips: r.trips, wins: r.wins, losses: r.losses,
    })).reverse();
    const sum = (k) => rows.reduce((a, r) => a + r[k], 0);
    const trips = sum('trips'), wins = sum('wins'), days = rows.length;
    const winDays = rows.filter((r) => r.pnl > 0).length;
    return {
      days, total_pnl: r1(sum('pnl')), trips, wins,
      win_rate: trips ? r1(wins / trips * 100) : null,
      day_win_rate: days ? r1(winDays / days * 100) : null,
      recent: rows.slice(0, 30),
    };
  }

  function getSession(sid) {
    const s = sessions.get(sid);
    if (!s) throw new Error('セッションが見つかりません（ページを開き直したときは、もう一度「この日で開始」を押してください）');
    return s;
  }

  async function call(path, body = {}) {
    const idx = await loadIndex();
    const datesOf = (sym) => (idx.symbols[sym] || []).map((date) => ({ date }));
    if (path === '/api/config') {
      return {
        provider: '自動更新（GitHub Actions）', range_note: idx.range_note, updated: idx.updated,
        ai_enabled: false, ai_model: '', watchlist: idx.watchlist || [], symbols: Object.keys(idx.symbols),
        add_url: idx.repo ? `https://github.com/${idx.repo}/actions/workflows/update-data.yml` : '',
      };
    }
    if (path === '/api/fetch' || path === '/api/update') {
      const symbol = normalize(body.symbol);
      if (!idx.symbols[symbol]) {
        throw new Error(`${symbol} はまだ登録されていません（登録済み: ${Object.keys(idx.symbols).join(', ') || 'なし'}）。「銘柄を追加」から追加できます`);
      }
      return { symbol, fetched: 0, dates: datesOf(symbol) };
    }
    if (path === '/api/stats') return stats();
    if (path === '/api/session') {
      const symbol = normalize(body.symbol);
      if (!(idx.symbols[symbol] || []).includes(body.date)) throw new Error(`${symbol} の ${body.date} は練習日として選べません`);
      const [day, refs] = await Promise.all([
        getJSON(`data/${encodeURIComponent(symbol)}/${body.date}.json`),
        getJSON(`data/_refs/${body.date}.json`).catch(() => ({})),
      ]);
      const s = new Session(day, refs, body.commission, body.slippage_ticks);
      sessions.clear(); // 同時に持つのは1セッションだけ
      sessions.set(s.id, s);
      return { state: s.state(), prev_close: s.prevClose, tick: day.tick || tickSize(s.prevClose || s.bars[0].o), refs: s.refsUntil() };
    }
    const m = path.match(/^\/api\/session\/([^/]+)\/(step|order|ai)$/);
    if (m) {
      const s = getSession(m[1]);
      if (m[2] === 'step') return s.step(Math.min(Math.max(Math.trunc(Number(body.n) || 1), 1), 400));
      if (m[2] === 'ai') throw new Error('AI分析はPC版でのみ使えます');
      let o = null;
      if (body.action === 'flatten') o = s.flatten();
      else if (body.action === 'cancel') s.pending = [];
      else o = s.order(body.action, Math.trunc(Number(body.qty) || 0));
      return { order: o, state: s.state() };
    }
    throw new Error(`未対応のAPI: ${path}`);
  }

  window.DTRLocal = { call };
})();
