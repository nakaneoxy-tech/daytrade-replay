// デイトレ練習リプレイ — フロントエンド
// サーバーは「進めた分の足」しか返さないので、ここでの指標計算も自動的に現在までのデータのみになる。
(() => {
  const $ = (id) => document.getElementById(id);
  const SPEEDS = [0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 20, 30, 45, 60];
  const MARKER_LABELS = 1;
  const MARKER_MODES = { recent: '約定: 直近のみ文字', arrows: '約定: 矢印のみ', off: '約定: 非表示' };
  const css = getComputedStyle(document.documentElement);
  const C = (n) => css.getPropertyValue(n).trim();

  const st = {
    cfg: null, symbol: null, sid: null, bars: [], refs: { nikkei: [], usdjpy: [] },
    tf: 1, playing: false, timer: null, busy: false, finished: false,
    tick: 1, prevClose: null, fills: [], state: null,
    markerMode: (() => { try { return localStorage.getItem('dtr.markerMode') || 'recent'; } catch { return 'recent'; } })(),
  };

  // ---------- util ----------
  const toTime = (s) => { // "YYYY-MM-DD HH:MM" (JST) → チャート用秒（JSTをUTCとして扱い表示をJSTに）
    const [d, t] = s.split(' ');
    const [y, m, dd] = d.split('-').map(Number);
    const [hh, mm] = t.split(':').map(Number);
    return Date.UTC(y, m - 1, dd, hh, mm) / 1000;
  };
  const yen = (v, sign = true) => {
    if (v == null || isNaN(v)) return '—';
    const s = Math.round(v).toLocaleString('ja-JP');
    return (sign && v > 0 ? '+' : '') + s;
  };
  const num = (v) => v == null ? '—' : Number(v).toLocaleString('ja-JP', { maximumFractionDigits: 2 });
  const cls = (el, v) => { el.classList.toggle('plus', v > 0); el.classList.toggle('minus', v < 0); };
  function toast(msg, ms = 2600) {
    const t = $('toast'); t.textContent = msg; t.classList.remove('hidden');
    clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add('hidden'), ms);
  }
  // サーバーなし版（スマホ用の静的サイト）では、同じAPIをブラウザ内のエンジン（local-backend.js）が処理する
  let localMode = false;
  async function detectMode() {
    try {
      const r = await fetch('api/config');
      localMode = !(r.ok && (r.headers.get('content-type') || '').includes('json'));
    } catch { localMode = true; }
    document.body.classList.toggle('local-mode', localMode);
  }
  async function api(path, body) {
    if (localMode) return window.DTRLocal.call(path, body);
    const r = await fetch(path.slice(1), body === undefined ? {} : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    const j = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
    if (!r.ok || j.error) throw new Error(j.error || `HTTP ${r.status}`);
    return j;
  }

  // ---------- charts ----------
  const LW = LightweightCharts;
  const baseOpts = {
    layout: { background: { color: C('--bg') }, textColor: C('--muted'), fontSize: 11 },
    grid: { vertLines: { color: '#1b2028' }, horzLines: { color: '#1b2028' } },
    rightPriceScale: { borderColor: C('--line') },
    timeScale: { borderColor: C('--line'), timeVisible: true, secondsVisible: false, rightOffset: 6 },
    crosshair: { mode: LW.CrosshairMode.Normal },
    localization: { locale: 'ja-JP' },
  };
  const main = LW.createChart($('mainChart'), { ...baseOpts, autoSize: true });
  const candle = main.addCandlestickSeries({
    upColor: C('--up'), downColor: C('--down'), borderUpColor: C('--up'), borderDownColor: C('--down'),
    wickUpColor: C('--up'), wickDownColor: C('--down'),
  });
  candle.priceScale().applyOptions({ scaleMargins: { top: 0.06, bottom: 0.24 } });
  const volume = main.addHistogramSeries({ priceScaleId: 'vol', priceFormat: { type: 'volume' }, lastValueVisible: false, priceLineVisible: false });
  main.priceScale('vol').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
  const lineOpt = (color, w = 1.5) => ({ color, lineWidth: w, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  const ma5 = main.addLineSeries(lineOpt(C('--ma5')));
  const ma25 = main.addLineSeries(lineOpt(C('--ma25')));
  const vwap = main.addLineSeries({ ...lineOpt(C('--vwap'), 2), lineStyle: LW.LineStyle.Solid });
  let prevLine = null, avgLine = null;

  const refOpts = { ...baseOpts, autoSize: true, rightPriceScale: { borderColor: C('--line'), scaleMargins: { top: 0.1, bottom: 0.1 } },
    timeScale: { ...baseOpts.timeScale, rightOffset: 2 }, handleScroll: false, handleScale: false };
  const nk = LW.createChart($('nkChart'), refOpts);
  const nkLine = nk.addLineSeries({ color: '#c9d1dc', lineWidth: 1.5, priceLineVisible: false });
  const fx = LW.createChart($('fxChart'), refOpts);
  const fxLine = fx.addLineSeries({ color: '#e7b75a', lineWidth: 1.5, priceLineVisible: false, priceFormat: { type: 'price', precision: 3, minMove: 0.001 } });

  // ---------- 集計・指標（現在までの足のみ） ----------
  function aggregate(bars, tf) {
    if (tf === 1) return bars.map((b) => ({ ...b, time: toTime(b.t), last1m: b }));
    const out = [];
    for (const b of bars) {
      const t = toTime(b.t);
      const bucket = Math.floor(t / (tf * 60)) * tf * 60;
      const cur = out[out.length - 1];
      if (cur && cur.time === bucket) {
        cur.h = Math.max(cur.h, b.h); cur.l = Math.min(cur.l, b.l); cur.c = b.c; cur.v += b.v; cur.last1m = b;
        if (cur.allFilled && !b.filled) { cur.o = b.o; cur.h = b.h; cur.l = b.l; }
        cur.allFilled = cur.allFilled && b.filled;
      } else {
        out.push({ time: bucket, o: b.o, h: b.h, l: b.l, c: b.c, v: b.v, allFilled: b.filled, last1m: b });
      }
    }
    return out;
  }
  function sma(rows, n) {
    const out = []; let sum = 0;
    rows.forEach((r, i) => {
      sum += r.c; if (i >= n) sum -= rows[i - n].c;
      if (i >= n - 1) out.push({ time: r.time, value: sum / n });
    });
    return out;
  }
  function vwapMap(bars) { // 1分足の t → その時点までの当日VWAP
    const m = new Map(); let pv = 0, vv = 0;
    for (const b of bars) {
      if (b.v > 0) { pv += (b.h + b.l + b.c) / 3 * b.v; vv += b.v; }
      if (vv > 0) m.set(b.t, pv / vv);
    }
    return m;
  }

  function render(fit = false) {
    const rows = aggregate(st.bars, st.tf);
    // 寄り前の補完足（前日終値で埋めた足）は時刻だけ確保して描かない（価格軸が潰れるのを防ぐ）。移動平均にも含めない
    const firstReal = rows.findIndex((r) => (r.allFilled ?? r.filled) === false);
    const isPreOpen = (i) => firstReal < 0 || i < firstReal;
    candle.setData(rows.map((r, i) => isPreOpen(i) ? { time: r.time } : { time: r.time, open: r.o, high: r.h, low: r.l, close: r.c }));
    volume.setData(rows.map((r, i) => isPreOpen(i) ? { time: r.time } : { time: r.time, value: r.v, color: r.c >= r.o ? 'rgba(224,72,74,.45)' : 'rgba(47,158,106,.45)' }));
    const maRows = firstReal < 0 ? [] : rows.slice(firstReal);
    ma5.setData(sma(maRows, 5));
    ma25.setData(sma(maRows, 25));
    const vm = vwapMap(st.bars);
    vwap.setData(rows.filter((r) => vm.has(r.last1m.t)).map((r) => ({ time: r.time, value: vm.get(r.last1m.t) })));
    // 約定マーカー：同じ足・同じ売買はまとめ、文字は直近 MARKER_LABELS 件だけ
    const step = st.tf * 60;
    const groups = new Map();
    if (st.markerMode !== 'off') {
      for (const f of st.fills) {
        const time = Math.floor(toTime(f.time) / step) * step;
        const key = `${time}-${f.side}`;
        const g = groups.get(key) || { time, side: f.side, qty: 0, amt: 0 };
        g.qty += f.qty; g.amt += f.qty * f.price;
        groups.set(key, g);
      }
    }
    const marks = [...groups.values()].sort((a, b) => a.time - b.time);
    candle.setMarkers(marks.map((g, i) => ({
      time: g.time,
      position: g.side === 'buy' ? 'belowBar' : 'aboveBar',
      color: g.side === 'buy' ? C('--buy') : C('--sell'),
      shape: g.side === 'buy' ? 'arrowUp' : 'arrowDown',
      size: 0.6,
      text: st.markerMode === 'recent' && i >= marks.length - MARKER_LABELS
        ? `${g.side === 'buy' ? '買' : '売'}${g.qty} @${num(Math.round(g.amt / g.qty))}` : '',
    })));
    if (avgLine) { candle.removePriceLine(avgLine); avgLine = null; }
    if (st.state && st.state.pos) {
      avgLine = candle.createPriceLine({ price: st.state.avg, color: '#e7b75a', lineWidth: 1, lineStyle: LW.LineStyle.Dashed, axisLabelVisible: true, title: `建値 ${st.state.pos > 0 ? '買' : '売'}${Math.abs(st.state.pos)}` });
    }
    if (fit) main.timeScale().fitContent();
    else if (rows.length) {
      const r = main.timeScale().getVisibleLogicalRange();
      // 最新の足が画面外なら追従
      if (!r || r.to < rows.length - 3) main.timeScale().scrollToRealTime();
    }
    requestAnimationFrame(drawVP);
  }

  function renderRefs() {
    const conv = (rows) => rows.map((r) => ({ time: toTime(r.t), value: r.c }));
    nkLine.setData(conv(st.refs.nikkei));
    fxLine.setData(conv(st.refs.usdjpy));
    nk.timeScale().fitContent(); fx.timeScale().fitContent();
    const last = (a) => a.length ? a[a.length - 1] : null;
    const n = last(st.refs.nikkei), f = last(st.refs.usdjpy);
    $('nkLast').textContent = n ? `${num(n.c)}（${n.t.slice(11)}）` : '—';
    $('fxLast').textContent = f ? `${Number(f.c).toFixed(3)}（${f.t.slice(11)}）` : '—';
  }

  // 価格帯別出来高（現在までの1分足のみ）。キャンバスでチャート右側に横棒を重ねる
  function drawVP() {
    const cv = $('vpCanvas'), wrap = $('mainWrap');
    const dpr = window.devicePixelRatio || 1;
    const W = wrap.clientWidth, H = wrap.clientHeight;
    cv.width = W * dpr; cv.height = H * dpr; cv.style.width = W + 'px'; cv.style.height = H + 'px';
    const g = cv.getContext('2d'); g.scale(dpr, dpr); g.clearRect(0, 0, W, H);
    const real = st.bars.filter((b) => b.v > 0);
    if (!real.length) return;
    let lo = Infinity, hi = -Infinity;
    for (const b of real) { lo = Math.min(lo, b.l); hi = Math.max(hi, b.h); }
    let bin = Math.max(st.tick, (hi - lo) / 40);
    bin = Math.ceil(bin / st.tick) * st.tick;
    const start = Math.floor(lo / bin) * bin;
    const vols = new Map();
    for (const b of real) {
      // 足の高安に出来高を均等配分
      const a = Math.floor((b.l - start) / bin), z = Math.floor((b.h - start) / bin);
      const share = b.v / (z - a + 1);
      for (let k = a; k <= z; k++) vols.set(k, (vols.get(k) || 0) + share);
    }
    const max = Math.max(...vols.values());
    const scaleW = main.priceScale('right').width();
    const right = W - scaleW - 2, maxW = (W - scaleW) * 0.22;
    const lastPrice = real[real.length - 1].c;
    for (const [k, v] of vols) {
      const p0 = start + k * bin, p1 = p0 + bin;
      const y0 = candle.priceToCoordinate(p1), y1 = candle.priceToCoordinate(p0);
      if (y0 == null || y1 == null) continue;
      const w = (v / max) * maxW;
      const inBin = lastPrice >= p0 && lastPrice < p1;
      g.fillStyle = inBin ? 'rgba(231,183,90,.45)' : 'rgba(140,160,190,.22)';
      g.fillRect(right - w, Math.min(y0, y1) + 0.5, w, Math.max(1, Math.abs(y1 - y0) - 1));
    }
  }
  main.timeScale().subscribeVisibleLogicalRangeChange(() => requestAnimationFrame(drawVP));
  new ResizeObserver(() => requestAnimationFrame(drawVP)).observe($('mainWrap'));
  $('mainWrap').addEventListener('wheel', () => requestAnimationFrame(drawVP), { passive: true });
  $('mainWrap').addEventListener('mousemove', (e) => { if (e.buttons) requestAnimationFrame(drawVP); });

  // ---------- UI状態 ----------
  function setEnabled(on) {
    for (const id of ['btnPlay', 'btnStep', 'btnStep10', 'btnBuy', 'btnSell', 'btnFlat', 'btnCancel']) $(id).disabled = !on;
    document.querySelectorAll('#dock button[data-act]').forEach((b) => (b.disabled = !on));
  }
  function updatePanel() {
    const s = st.state; if (!s) return;
    $('clock').textContent = s.cursor < 0 ? '寄り前' : s.now.slice(11);
    $('posTxt').textContent = s.pos === 0 ? '0' : `${s.pos > 0 ? '買' : '売'} ${Math.abs(s.pos)}株`;
    $('posTxt').className = s.pos > 0 ? 'plus' : s.pos < 0 ? 'minus' : '';
    $('avgTxt').textContent = s.pos ? num(s.avg) : '—';
    $('lastTxt').textContent = num(s.last);
    $('unrTxt').textContent = yen(s.unrealized); cls($('unrTxt'), s.unrealized);
    $('relTxt').textContent = yen(s.realized); cls($('relTxt'), s.realized);
    $('feeTxt').textContent = yen(s.commission_total, false);
    $('pendingTxt').textContent = s.pending.length
      ? '待機中の注文: ' + s.pending.map((o) => `${o.side === 'buy' ? '買' : '売'}${o.qty}`).join(' / ') + '（次の足の始値で約定）' : '';
    // スマホ用の下部バー
    const total = s.realized + s.unrealized;
    $('dockPos').textContent = (s.pos === 0 ? '建玉なし' : `${s.pos > 0 ? '買' : '売'}${Math.abs(s.pos)} @${num(s.avg)}`)
      + (s.pending.length ? `・注文待ち${s.pending.length}` : '');
    $('dockPnl').textContent = `${yen(total)}円`; cls($('dockPnl'), total);
  }
  function renderFills() {
    const el = $('fills');
    if (!st.fills.length) { el.innerHTML = '<div class="hint">まだ約定はありません</div>'; return; }
    el.innerHTML = st.fills.slice().reverse().map((f) => `
      <div class="fill" title="${f.reason}">
        <span class="mono">${f.time.slice(11)}</span>
        <span class="${f.side === 'buy' ? 'b' : 's'}">${f.side === 'buy' ? '買' : '売'}</span>
        <span class="mono">${f.qty}株 @${num(f.price)}</span>
        <span class="mono ${f.realized > 0 ? 'plus' : f.realized < 0 ? 'minus' : ''}">${f.realized ? yen(f.realized) : ''}</span>
      </div>`).join('');
  }
  async function loadStats() {
    try {
      const s = await api('/api/stats');
      $('stats').innerHTML = s.days ? `
        <div>累計損益 <b class="${s.total_pnl > 0 ? 'plus' : s.total_pnl < 0 ? 'minus' : ''}">${yen(s.total_pnl)}円</b></div>
        <div>勝率 <b>${s.win_rate ?? '—'}%</b>（${s.wins}/${s.trips} 往復）・日次勝率 <b>${s.day_win_rate ?? '—'}%</b>（${s.days}日）</div>
        <table>${s.recent.slice(0, 8).map((r) => `<tr><td>${r.trade_date}</td><td>${r.symbol}</td>
          <td class="${r.pnl > 0 ? 'plus' : r.pnl < 0 ? 'minus' : ''}" style="text-align:right">${yen(r.pnl)}</td><td>${r.wins}勝${r.losses}敗</td></tr>`).join('')}</table>`
        : '<span class="hint">まだ記録はありません（1日を最後まで再生すると保存されます）</span>';
    } catch (e) { $('stats').textContent = e.message; }
  }

  // ---------- データ取得 ----------
  function fillDates(dates) {
    const sel = $('dateSel');
    sel.innerHTML = dates.length
      ? dates.map((d) => `<option value="${d.date}">${d.date}（${'日月火水木金土'[new Date(d.date + 'T00:00:00').getDay()]}）</option>`).join('')
      : '<option value="">練習できる日がありません</option>';
    $('btnStart').disabled = !dates.length;
  }
  async function doFetch(update = false) {
    const raw = $('symbol').value.trim();
    if (!raw) return toast('銘柄コードを入力してください');
    stopPlay();
    const btn = update ? $('btnUpdate') : $('btnFetch');
    const label = btn.textContent;
    btn.disabled = true; btn.textContent = localMode ? '…' : '取得中…';
    try {
      const j = await api(update ? '/api/update' : '/api/fetch', { symbol: raw });
      st.symbol = j.symbol;
      fillDates(j.dates);
      toast(localMode ? `${j.symbol}: 練習できる日 ${j.dates.length}日` : `${j.symbol}: ${j.fetched}本取得・練習可能 ${j.dates.length}日（一括更新リストに登録済み）`);
    } catch (e) { toast('取得失敗: ' + e.message, 5000); }
    finally { btn.disabled = false; btn.textContent = label; }
  }

  async function startDay() {
    const date = $('dateSel').value;
    if (!st.symbol || !date) return;
    stopPlay();
    try {
      const j = await api('/api/session', {
        symbol: st.symbol, date,
        commission: Number($('commission').value) || 0,
        slippage_ticks: Number($('slip').value) || 0,
      });
      Object.assign(st, { sid: j.state.id, bars: [], fills: [], state: j.state, finished: false,
        tick: j.tick || 1, prevClose: j.prev_close, refs: { nikkei: j.refs.nikkei || [], usdjpy: j.refs.usdjpy || [] } });
      $('title').textContent = `${st.symbol}  ${date}`;
      $('emptyMsg').textContent = '寄り前です。▶再生 か +1分 で進めます（寄り前の注文は最初の約定足の始値で約定）';
      $('emptyMsg').style.display = '';
      if (prevLine) candle.removePriceLine(prevLine);
      prevLine = st.prevClose ? candle.createPriceLine({ price: st.prevClose, color: '#6b7584', lineWidth: 1, lineStyle: LW.LineStyle.Dotted, axisLabelVisible: true, title: '前日終値' }) : null;
      setEnabled(true);
      render(true); renderRefs(); renderFills(); updatePanel();
      $('modal').classList.add('hidden');
      document.body.classList.remove('show-setup');
      document.body.classList.add('started');
      document.activeElement && document.activeElement.blur();
    } catch (e) { toast(e.message, 5000); }
  }

  // ---------- 再生 ----------
  async function step(n = 1) {
    if (!st.sid || st.busy || st.finished) return;
    st.busy = true;
    try {
      const j = await api(`/api/session/${st.sid}/step`, { n });
      if (j.bars.length) {
        st.bars.push(...j.bars);
        $('emptyMsg').style.display = 'none';
      }
      st.refs.nikkei.push(...(j.refs.nikkei || []));
      st.refs.usdjpy.push(...(j.refs.usdjpy || []));
      if (j.fills.length) {
        st.fills.push(...j.fills);
        for (const f of j.fills) toast(`約定 ${f.time.slice(11)} ${f.side === 'buy' ? '買' : '売'}${f.qty}株 @${num(f.price)}${f.realized ? `（${yen(f.realized)}円）` : ''}`);
        renderFills();
      }
      st.state = j.state;
      render(); renderRefs(); updatePanel();
      if (j.state.finished) finishDay(j.state.result);
    } catch (e) { toast(e.message, 5000); stopPlay(); }
    finally { st.busy = false; }
  }
  const speed = () => SPEEDS[Number($('speed').value)];
  function schedule() {
    clearTimeout(st.timer);
    st.timer = setTimeout(async () => {
      if (!st.playing) return;
      await step(1);
      if (st.playing && !st.finished) schedule();
    }, 60000 / speed());
  }
  function startPlay() { if (!st.sid || st.finished) return; st.playing = true; setPlayLabel(true); step(1).then(() => st.playing && schedule()); }
  function stopPlay() { st.playing = false; clearTimeout(st.timer); setPlayLabel(false); }
  function setPlayLabel(on) {
    $('btnPlay').textContent = on ? '⏸ 一時停止' : '▶ 再生';
    document.querySelector('#dock [data-act="play"]').textContent = on ? '⏸' : '▶';
  }
  const togglePlay = () => (st.playing ? stopPlay() : startPlay());
  function onSpeed() { $('speedLbl').textContent = `${speed()}倍`; if (st.playing) schedule(); }

  // ---------- 売買 ----------
  async function order(action) {
    if (!st.sid || st.finished) return;
    const qty = Number($('qty').value);
    if ((action === 'buy' || action === 'sell') && (!qty || qty % 100)) return toast('株数は100株単位で指定してください');
    try {
      const j = await api(`/api/session/${st.sid}/order`, { action, qty });
      st.state = j.state; updatePanel();
      if (action === 'flatten' && !j.order) toast('建玉はありません');
      else if (action === 'cancel') toast('待機中の注文を取り消しました');
      else toast(`${action === 'buy' ? '買い' : action === 'sell' ? '売り' : '全決済'}注文 → 次の足の始値で約定`);
    } catch (e) { toast(e.message, 4000); }
  }

  // ---------- 終了 ----------
  function finishDay(r) {
    stopPlay(); st.finished = true;
    setEnabled(false);
    $('mTitle').textContent = `${r.symbol} ${r.date} の結果`;
    $('mBody').innerHTML = `
      <div class="big ${r.pnl > 0 ? 'plus' : r.pnl < 0 ? 'minus' : ''}">${yen(r.pnl)}円</div>
      <div>往復 ${r.trips}回　${r.wins}勝 ${r.losses}敗　勝率 ${r.win_rate ?? '—'}%　手数料 ${yen(r.commission, false)}円
        <span class="hint">（手数料 ${r.settings.commission}円/約定・スリッページ ${r.settings.slippage_ticks}ティック）</span></div>
      <table class="tbl"><tr><th>時刻</th><th>売買</th><th>株数</th><th>約定値</th><th>実現損益</th><th>建玉後</th><th>内容</th></tr>
      ${r.fills.map((f) => `<tr><td>${f.time.slice(11)}</td><td>${f.side === 'buy' ? '買' : '売'}</td><td>${f.qty}</td><td>${num(f.price)}</td>
        <td class="${f.realized > 0 ? 'plus' : f.realized < 0 ? 'minus' : ''}">${f.realized ? yen(f.realized) : ''}</td><td>${f.pos_after}</td><td>${f.reason}</td></tr>`).join('') || '<tr><td colspan="7">売買なし</td></tr>'}
      </table>`;
    $('aiOut').textContent = '';
    $('modal').classList.remove('hidden');
    loadStats();
  }
  async function runAI() {
    $('btnAI').disabled = true; $('aiOut').textContent = 'AIが分析中…（数十秒かかることがあります）';
    try { $('aiOut').textContent = (await api(`/api/session/${st.sid}/ai`, {})).text; }
    catch (e) { $('aiOut').textContent = e.message; }
    finally { $('btnAI').disabled = !st.cfg.ai_enabled; }
  }

  // ---------- イベント ----------
  $('btnFetch').onclick = () => doFetch(false);
  $('btnUpdate').onclick = () => doFetch(true);
  $('symbol').addEventListener('keydown', (e) => { if (e.key === 'Enter') doFetch(false); });
  $('btnStart').onclick = startDay;
  $('btnPlay').onclick = togglePlay;
  $('btnStep').onclick = () => step(1);
  $('btnStep10').onclick = () => step(10);
  $('speed').oninput = onSpeed;
  $('btnBuy').onclick = () => order('buy');
  $('btnSell').onclick = () => order('sell');
  $('btnFlat').onclick = () => order('flatten');
  $('btnCancel').onclick = () => order('cancel');
  $('mClose').onclick = () => $('modal').classList.add('hidden');
  $('btnAI').onclick = runAI;
  // スマホ用の下部バー・表示切替
  const dockActs = { play: togglePlay, step: () => step(1), buy: () => order('buy'), sell: () => order('sell'), flat: () => order('flatten') };
  document.querySelectorAll('#dock button[data-act]').forEach((b) => (b.onclick = () => dockActs[b.dataset.act]()));
  $('btnRefs').onclick = () => { document.body.classList.toggle('show-refs'); requestAnimationFrame(drawVP); };
  $('btnSetup').onclick = () => document.body.classList.toggle('show-setup');
  function setTf(tf) {
    st.tf = tf;
    document.querySelectorAll('#tfBtns button').forEach((b) => b.classList.toggle('on', Number(b.dataset.tf) === tf));
    render(true);
  }
  document.querySelectorAll('#tfBtns button').forEach((b) => (b.onclick = () => setTf(Number(b.dataset.tf))));
  function cycleMarkers() {
    const keys = Object.keys(MARKER_MODES);
    st.markerMode = keys[(keys.indexOf(st.markerMode) + 1) % keys.length];
    try { localStorage.setItem('dtr.markerMode', st.markerMode); } catch { /* 保存できなくても動作に影響なし */ }
    $('btnMarkers').textContent = MARKER_MODES[st.markerMode];
    render();
  }
  $('btnMarkers').textContent = MARKER_MODES[st.markerMode] || MARKER_MODES.recent;
  $('btnMarkers').onclick = cycleMarkers;

  document.addEventListener('keydown', (e) => {
    const tag = (e.target.tagName || '').toLowerCase();
    if ((tag === 'input' && e.target.type !== 'range') || tag === 'select' || tag === 'textarea') return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const k = e.key.toLowerCase();
    const map = {
      ' ': togglePlay, arrowright: () => step(e.shiftKey ? 10 : 1),
      b: () => order('buy'), s: () => order('sell'), x: () => order('flatten'), c: () => order('cancel'),
      m: cycleMarkers,
      1: () => setTf(1), 3: () => setTf(3), 5: () => setTf(5), f: () => setTf(15),
      arrowup: () => ($('qty').value = Number($('qty').value) + 100),
      arrowdown: () => ($('qty').value = Math.max(100, Number($('qty').value) - 100)),
      escape: () => $('modal').classList.add('hidden'),
    };
    if (map[k]) { e.preventDefault(); map[k](); }
  });

  // ---------- 初期化 ----------
  (async () => {
    onSpeed();
    await detectMode();
    try {
      st.cfg = await api('/api/config');
      $('rangeNote').textContent = `データ: ${st.cfg.provider}　※ ${st.cfg.range_note}　当日分は大引け(15:30)後に練習日へ追加されます。`
        + (st.cfg.updated ? `　最終更新 ${st.cfg.updated}` : '');
      $('btnAI').disabled = !st.cfg.ai_enabled;
      $('aiNote').textContent = st.cfg.ai_enabled
        ? `Claude API（${st.cfg.ai_model}）を使用・従量課金です（1回あたり数円〜数十円程度）。押したときだけ送信されます。`
        : localMode ? 'AI分析はPC版（python app.py で起動）でのみ使えます。'
          : 'ANTHROPIC_API_KEY が未設定のため無効です（設定するとClaude APIで講評。従量課金）。';
      if (st.cfg.watchlist.length) $('symbol').title = '一括更新リスト: ' + st.cfg.watchlist.join(', ');
      if (localMode) {
        // サーバーなし版：登録済みの銘柄から選ぶ。新しい銘柄は GitHub Actions で追加する
        $('btnFetch').textContent = '開く';
        $('symList').innerHTML = st.cfg.symbols.map((s) => `<option value="${s.replace('.T', '')}">`).join('');
        $('emptyMsg').textContent = '銘柄を選んで「開く」→ 練習日を選んで「この日で開始」';
        if (st.cfg.add_url) { $('addSym').href = st.cfg.add_url; $('addSym').classList.remove('hidden'); }
        if (st.cfg.symbols.length) {
          if (!st.cfg.symbols.includes($('symbol').value.trim() + '.T')) $('symbol').value = st.cfg.symbols[0].replace('.T', '');
          await doFetch(false);
        }
      }
    } catch (e) { toast((localMode ? 'データを読み込めません: ' : 'サーバーに接続できません: ') + e.message, 6000); }
    loadStats();
  })();
})();
