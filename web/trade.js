/* ============================================================================
   Phenom Trade Desk
   - Plan + size + track trades from WATCHING -> TRIGGERED -> CLOSED.
   - Two modes:
       LOCAL  (default): everything lives in this browser (localStorage). Paper.
       WORKER (optional): paste a Railway worker URL -> trades are stored and
                          monitored server-side; this page is just the control
                          panel. Paper now, real broker (Upstox) later.
   - No real broker order is ever placed from this page. The worker is paper
     until a broker is explicitly connected and confirmed there.
   ========================================================================== */
(() => {
  "use strict";

  const LS_TRADES  = "phenom_trades_v1";
  const LS_API     = "phenom_trade_api";
  const LS_CAPITAL = "phenom_trade_capital";
  const LS_JOURNAL = "phenom_journal_v1";   // per-trade journal annotations, keyed by trade id
  const DEFAULT_CAPITAL = 100000;

  const DEFAULT_SL_PCT = 4;     // used when the user leaves Stop blank
  const POLL_MS        = 15000; // worker-mode refresh cadence

  const state = {
    apiUrl:  (localStorage.getItem(LS_API) || "").replace(/\/+$/, ""),
    capital: +(localStorage.getItem(LS_CAPITAL) || DEFAULT_CAPITAL),
    open:    [],
    closed:  [],
    ann:     {},          // { tradeId: {setup, grade, emotion, wentRight, wentWrong, lesson, tags, notes:[{ts,text}]} }
    tickers: {},          // { SYMBOL: "NSE"|"BSE" } — full active universe, NSE-preferred
    tnames: {},           // { SYMBOL: "Company Name" }
    tickerList: [],       // [[symbol, exchange, name], ...] for the autocomplete
    pollTimer: null,
  };
  const usingWorker = () => !!state.apiUrl;
  const subs = [];        // journal/other listeners, fired on any trade change
  function notify() { subs.forEach((f) => { try { f(); } catch (e) { console.error(e); } }); }

  const $ = (id) => document.getElementById(id);

  /* ----------------------------------------------------------- formatters */
  const rupee = (v) => {
    if (v == null || Number.isNaN(v)) return "—";
    return "₹" + Number(v).toLocaleString("en-IN", { maximumFractionDigits: 0 });
  };
  const price = (v) => {
    if (v == null || Number.isNaN(v)) return "—";
    return Number(v).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  };
  const pnlSpan = (v, txt) => {
    if (v == null || Number.isNaN(v)) return `<span class="muted">—</span>`;
    const cls = v > 0 ? "pos" : v < 0 ? "neg" : "muted";
    const sign = v > 0 ? "+" : "";
    return `<span class="${cls}">${sign}${txt}</span>`;
  };
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const shortTime = (iso) => {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "—";
    return d.toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
  };

  const IS_MOBILE = /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent);
  const tvLink = (sym, exch) => {
    const e = exch === "BSE" ? "BSE" : "NSE";
    return IS_MOBILE
      ? `https://www.tradingview.com/symbols/${e}-${encodeURIComponent(sym)}/`
      : `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(e + ":" + sym)}`;
  };
  const tvAttrs = IS_MOBILE ? `rel="noopener"` : `target="_blank" rel="noopener"`;

  /* ----------------------------------------------------------- trade math */
  // Pure: given the raw inputs, return all derived numbers. Same formula the
  // worker uses, so LOCAL and WORKER modes agree.
  function computeTrade(inp) {
    const dir = inp.direction === "short" ? -1 : 1;
    const level  = +inp.level;
    const buffer = inp.buffer == null || inp.buffer === "" ? 1 : +inp.buffer;
    const entry  = level * (1 + dir * buffer / 100);

    let stop;
    if (inp.sl != null && inp.sl !== "" && +inp.sl > 0) {
      stop = +inp.sl;
    } else {
      stop = entry * (1 - dir * DEFAULT_SL_PCT / 100);
    }
    const riskPerShare = Math.abs(entry - stop);
    const slPct = entry > 0 ? (riskPerShare / entry) * 100 : 0;

    // capital + risk% default if blank — only ticker + level are truly required
    const capital = inp.capital != null && +inp.capital > 0 ? +inp.capital : DEFAULT_CAPITAL;
    const riskPct = inp.risk == null || inp.risk === "" ? 1 : +inp.risk;
    const riskRs = capital * riskPct / 100;
    const qty = riskPerShare > 0 ? Math.floor(riskRs / riskPerShare) : 0;
    const posValue = qty * entry;

    const rr = inp.rr == null || inp.rr === "" ? 3 : +inp.rr;
    const target = entry + dir * riskPerShare * rr;
    const rewardRs = qty * riskPerShare * rr;
    const deployPct = capital > 0 ? (posValue / capital) * 100 : 0;

    return { dir, entry, stop, buffer, riskPerShare, slPct, capital, riskPct, riskRs, qty, posValue, rr, target, rewardRs, deployPct };
  }

  // Open P&L for a triggered trade given a mark price.
  function openPnl(t, mark) {
    if (mark == null || Number.isNaN(mark) || t.state !== "TRIGGERED") return null;
    const sign = t.direction === "short" ? -1 : 1;
    return (mark - t.entry) * sign * t.qty;
  }

  /* ----------------------------------------------------------- local store */
  function loadLocal() {
    try {
      const d = JSON.parse(localStorage.getItem(LS_TRADES) || "{}");
      state.open = Array.isArray(d.open) ? d.open : [];
      state.closed = Array.isArray(d.closed) ? d.closed : [];
    } catch { state.open = []; state.closed = []; }
  }
  function saveLocal() {
    localStorage.setItem(LS_TRADES, JSON.stringify({ open: state.open, closed: state.closed }));
  }
  const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 6);

  // ---------- journal annotations (work in BOTH local + worker modes) ----------
  function loadAnn() {
    try { state.ann = JSON.parse(localStorage.getItem(LS_JOURNAL) || "{}") || {}; }
    catch { state.ann = {}; }
  }
  function saveAnn() { localStorage.setItem(LS_JOURNAL, JSON.stringify(state.ann)); }

  // Build a timeline for a trade: prefer its own events[] (local mode logs them),
  // else reconstruct from timestamps (worker mode), then merge in journal notes.
  function eventsFor(t) {
    const ev = Array.isArray(t.events) && t.events.length
      ? t.events.slice()
      : [
          t.added       && { ts: t.added,       type: "CREATED" },
          t.triggeredAt && { ts: t.triggeredAt, type: "TRIGGERED" },
          t.closedAt    && { ts: t.closedAt,    type: "CLOSED", note: t.result },
        ].filter(Boolean);
    const ann = state.ann[t.id];
    if (ann && Array.isArray(ann.notes)) {
      ann.notes.forEach((n) => ev.push({ ts: n.ts, type: "NOTE", note: n.text }));
    }
    return ev.sort((a, b) => new Date(a.ts) - new Date(b.ts));
  }

  // Public API consumed by the Journal tab (journal.js).
  window.PhenomTrades = {
    getAll() {
      const decorate = (t) => ({ ...t, _ann: state.ann[t.id] || {}, _events: eventsFor(t) });
      return { open: state.open.map(decorate), closed: state.closed.map(decorate), mode: usingWorker() ? "worker" : "local" };
    },
    annotate(id, fields) {
      // save silently — re-rendering on every field blur would detach the card
      // mid-edit and lose focus. The journal updates chips on its next render.
      state.ann[id] = { ...(state.ann[id] || {}), ...fields };
      saveAnn();
    },
    addNote(id, text) {
      if (!text || !text.trim()) return;
      const a = state.ann[id] || {};
      a.notes = Array.isArray(a.notes) ? a.notes : [];
      a.notes.push({ ts: new Date().toISOString(), text: text.trim() });
      state.ann[id] = a; saveAnn(); notify();
    },
    subscribe(fn) { if (typeof fn === "function") subs.push(fn); },
  };

  /* ----------------------------------------------------------- ticker autocomplete
     Custom dropdown (drops right below the field, shows SYMBOL + company name).
     Native <datalist> positioned itself off to the side and showed no names. */
  async function loadTickers() {
    try {
      const _usMkt = localStorage.getItem("phenom_market") === "us";
      const r = await fetch(`../data/tickers${_usMkt ? "_us" : ""}.json`, { cache: "no-store" });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const d = await r.json();
      state.tickerList = d.tickers || [];
      state.tickers = {}; state.tnames = {};
      for (const [s, e, n] of state.tickerList) { state.tickers[s] = e; state.tnames[s] = n; }
    } catch (e) { /* file may not exist yet — manual entry still works */ }
  }
  // default the exchange to where the symbol is listed (NSE if available, else BSE)
  function autoExchange() {
    const exch = state.tickers[($("fTicker").value || "").trim().toUpperCase()];
    if (exch === "NSE" || exch === "BSE") $("fExchange").value = exch;
  }

  const AC = { items: [], idx: -1 };
  function acFilter(q) {
    q = q.trim().toUpperCase();
    if (!q) return [];
    const starts = [], contains = [];
    for (const row of state.tickerList) {
      const [s, , n] = row;
      if (s.startsWith(q)) starts.push(row);
      else if (s.includes(q) || (n && n.toUpperCase().includes(q))) contains.push(row);
      if (starts.length >= 60) break;
    }
    return starts.concat(contains).slice(0, 60);
  }
  function acRender() {
    const dd = $("tickerDD");
    const items = acFilter($("fTicker").value);
    AC.items = items; AC.idx = -1;
    if (!items.length) { dd.classList.add("hidden"); dd.innerHTML = ""; $("fTicker").setAttribute("aria-expanded", "false"); return; }
    dd.innerHTML = items.map((r, i) => {
      const [s, e, n] = r;
      return `<div class="ac-item" data-i="${i}" data-sym="${s}">
        <span class="ac-sym">${esc(s)} <span class="ac-exch">${e}</span></span>
        <span class="ac-name">${esc(n || "")}</span></div>`;
    }).join("");
    dd.classList.remove("hidden");
    $("fTicker").setAttribute("aria-expanded", "true");
  }
  function acClose() { $("tickerDD").classList.add("hidden"); AC.idx = -1; $("fTicker").setAttribute("aria-expanded", "false"); }
  function acHighlight() {
    const dd = $("tickerDD");
    [...dd.children].forEach((c, i) => c.classList.toggle("active", i === AC.idx));
    if (AC.idx >= 0 && dd.children[AC.idx]) dd.children[AC.idx].scrollIntoView({ block: "nearest" });
  }
  function acPick(sym) {
    $("fTicker").value = sym;
    autoExchange(); acClose(); renderCalc();
    $("fLevel").focus();
  }
  function acKeys(e) {
    const dd = $("tickerDD");
    if (dd.classList.contains("hidden")) return;
    if (e.key === "ArrowDown") { e.preventDefault(); AC.idx = Math.min(AC.idx + 1, AC.items.length - 1); acHighlight(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); AC.idx = Math.max(AC.idx - 1, 0); acHighlight(); }
    else if (e.key === "Enter") { if (AC.idx >= 0) { e.preventDefault(); acPick(AC.items[AC.idx][0]); } }
    else if (e.key === "Escape") { acClose(); }
  }

  /* ----------------------------------------------------------- worker calls */
  async function apiGet(path) {
    const r = await fetch(state.apiUrl + path, { cache: "no-store" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  }
  async function apiSend(path, method, body) {
    const r = await fetch(state.apiUrl + path, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json().catch(() => ({}));
  }

  /* ----------------------------------------------------------- store ops
     Each op works in either mode; LOCAL mutates arrays, WORKER hits the API
     then refreshes from the server (source of truth). */
  async function addTrade(inp) {
    const c = computeTrade(inp);
    if (usingWorker()) {
      await apiSend("/trades", "POST", {
        ticker: inp.ticker, exchange: inp.exchange, direction: inp.direction,
        level: +inp.level, buffer: +inp.buffer, sl: inp.sl === "" ? null : +inp.sl,
        capital: +inp.capital, risk_pct: +inp.risk, rr: +inp.rr, note: inp.note || "",
      });
      await refresh();
      return;
    }
    const nowIso = new Date().toISOString();
    state.open.unshift({
      id: uid(),
      ticker: inp.ticker.toUpperCase(), exchange: inp.exchange, direction: inp.direction,
      level: +inp.level, buffer: c.buffer,
      entry: round2(c.entry), stop: round2(c.stop), target: round2(c.target),
      qty: c.qty, riskPerShare: round2(c.riskPerShare), riskRs: Math.round(c.riskRs),
      rr: c.rr, capital: c.capital, riskPct: c.riskPct, note: inp.note || "",
      state: "WATCHING", mark: null, added: nowIso, triggeredAt: null,
      events: [{ ts: nowIso, type: "CREATED", note: `entry ${round2(c.entry)} · stop ${round2(c.stop)} · ${c.qty} sh · ${c.rr}R` }],
    });
    saveLocal(); render();
  }

  async function triggerTrade(id) {
    if (usingWorker()) { await apiSend(`/trades/${id}/trigger`, "POST"); return refresh(); }
    const t = state.open.find((x) => x.id === id);
    if (t) {
      t.state = "TRIGGERED"; t.triggeredAt = new Date().toISOString();
      if (t.mark == null) t.mark = t.entry;
      (t.events = t.events || []).push({ ts: t.triggeredAt, type: "TRIGGERED", note: `entry ${t.entry}` });
      saveLocal(); render();
    }
  }

  async function setMark(id, mark) {
    if (usingWorker()) return; // worker maintains marks itself
    const t = state.open.find((x) => x.id === id);
    if (t) { t.mark = mark; saveLocal(); renderStats(); renderOpen(); }
  }

  async function closeTrade(id, exit) {
    if (usingWorker()) { await apiSend(`/trades/${id}/close`, "POST", { exit }); return refresh(); }
    const i = state.open.findIndex((x) => x.id === id);
    if (i < 0) return;
    const t = state.open[i];
    const sign = t.direction === "short" ? -1 : 1;
    const pnlRs = (exit - t.entry) * sign * t.qty;
    const pnlPct = t.entry > 0 ? (exit - t.entry) * sign / t.entry * 100 : 0;
    const rMultiple = t.riskPerShare > 0 ? ((exit - t.entry) * sign) / t.riskPerShare : 0;
    let result = "MANUAL";
    if (sign === 1) { if (exit >= t.target) result = "TARGET"; else if (exit <= t.stop) result = "STOP"; }
    else           { if (exit <= t.target) result = "TARGET"; else if (exit >= t.stop) result = "STOP"; }
    const closedAt = new Date().toISOString();
    const events = (t.events || []).concat({ ts: closedAt, type: "CLOSED", note: `${result} @ ${round2(exit)} · ₹${Math.round(pnlRs)} (${round2(rMultiple)}R)` });
    state.closed.unshift({
      ...t, exit: round2(exit), pnlRs: Math.round(pnlRs), pnlPct: round2(pnlPct),
      rMultiple: round2(rMultiple), result, closedAt, events,
    });
    state.open.splice(i, 1);
    saveLocal(); render();
  }

  async function deleteTrade(id) {
    if (usingWorker()) { await apiSend(`/trades/${id}`, "DELETE"); return refresh(); }
    state.open = state.open.filter((x) => x.id !== id);
    saveLocal(); render();
  }

  async function refresh() {
    if (!usingWorker()) { loadLocal(); render(); return; }
    try {
      const d = await apiGet("/trades");
      state.open = d.open || [];
      state.closed = d.closed || [];
      setModePill(true, d.broker_mode || "paper");
      render();
    } catch (e) {
      setModePill(false);
      $("tradeModeDetail").textContent = "Worker unreachable — showing nothing. Check the URL.";
    }
  }

  const round2 = (v) => Math.round(v * 100) / 100;

  /* ----------------------------------------------------------- live calc strip */
  function readForm() {
    return {
      ticker: $("fTicker").value.trim(),
      exchange: $("fExchange").value,
      direction: $("fDirection").value,
      level: $("fLevel").value,
      buffer: $("fBuffer").value,
      sl: $("fSL").value,
      capital: $("fCapital").value,
      risk: $("fRisk").value,
      rr: $("fRR").value,
      note: $("fNote").value.trim(),
    };
  }
  function renderCalc() {
    const inp = readForm();
    const warn = $("calcWarn");
    if (!inp.level || +inp.level <= 0) {
      ["cEntry","cStop","cRiskShare","cQty","cValue","cRiskRs","cTarget","cReward"].forEach((id) => $(id).textContent = "—");
      warn.classList.add("hidden");
      return;
    }
    const c = computeTrade(inp);
    $("cEntry").textContent     = price(c.entry);
    $("cStop").textContent      = `${price(c.stop)}  (${c.slPct.toFixed(1)}%)`;
    $("cRiskShare").textContent = price(c.riskPerShare);
    $("cQty").textContent       = c.qty.toLocaleString("en-IN");
    $("cValue").textContent     = `${rupee(c.posValue)}  (${c.deployPct.toFixed(0)}%)`;
    $("cRiskRs").textContent    = rupee(c.riskRs);
    $("cTarget").textContent    = `${price(c.target)}  (${c.rr}R)`;
    $("cReward").textContent    = "+" + rupee(c.rewardRs).slice(1);

    const msgs = [];
    if (c.slPct > 8) msgs.push(`Stop is ${c.slPct.toFixed(1)}% away — wide. Smaller size, or tighten the stop.`);
    if (c.deployPct > 100) msgs.push(`Position needs ${c.deployPct.toFixed(0)}% of capital — more than you have. Lower risk % or widen the stop.`);
    if (c.qty <= 0) msgs.push(`Size works out to 0 shares — raise risk % or capital.`);
    if (msgs.length) { warn.innerHTML = msgs.map(esc).join("<br>"); warn.classList.remove("hidden"); }
    else warn.classList.add("hidden");
  }

  /* ----------------------------------------------------------- renderers */
  function render() { renderStats(); renderPending(); renderOpen(); renderClosed(); notify(); }

  function renderStats() {
    const live = state.open.filter((t) => t.state === "TRIGGERED");
    const pending = state.open.filter((t) => t.state !== "TRIGGERED");
    const closed = state.closed;
    $("sOpen").textContent = live.length;
    $("sClosed").textContent = closed.length;
    const pc = $("pendingCount"), oc = $("openCount");
    if (pc) pc.textContent = pending.length ? `(${pending.length})` : "";
    if (oc) oc.textContent = live.length ? `(${live.length})` : "";

    const wins = closed.filter((t) => (t.pnlRs ?? 0) > 0).length;
    $("sWin").textContent = closed.length ? `${Math.round(wins / closed.length * 100)}%` : "—";

    const net = closed.reduce((s, t) => s + (t.pnlRs ?? 0), 0);
    $("sPnl").innerHTML = closed.length ? pnlSpan(net, rupee(Math.abs(net)).replace("₹", "₹")) : "—";

    const rs = closed.map((t) => t.rMultiple).filter((v) => v != null && !Number.isNaN(v));
    const avgR = rs.length ? rs.reduce((a, b) => a + b, 0) / rs.length : null;
    $("sAvgR").innerHTML = avgR == null ? "—" : pnlSpan(avgR, `${avgR.toFixed(2)}R`);

    const openRisk = live.reduce((s, t) => s + (t.riskRs ?? 0), 0);
    $("sExposure").textContent = live.length ? rupee(openRisk) : "—";
  }

  const markCell = (t) => usingWorker()
    ? `<span class="mono">${t.mark != null ? price(t.mark) : "—"}</span>`
    : `<input class="mark-input mono" type="number" step="0.05" value="${t.mark != null ? t.mark : ""}" data-id="${t.id}" placeholder="mark" />`;
  const symCell = (t) => `<td class="sym"><a href="${tvLink(t.ticker, t.exchange)}" ${tvAttrs}>${esc(t.ticker)}</a>${t.note ? `<span class="row-note" title="${esc(t.note)}">●</span>` : ""}</td>`;
  const dirCell = (t) => `<td><span class="dir ${t.direction}">${t.direction === "short" ? "Short" : "Long"}</span></td>`;

  // PENDING orders (placed, waiting for a strong break of the level)
  function renderPending() {
    const body = $("pendingBody");
    const rows = state.open.filter((t) => t.state !== "TRIGGERED");
    $("pendingEmpty").classList.toggle("hidden", rows.length > 0);
    body.innerHTML = rows.map((t) => {
      const sign = t.direction === "short" ? -1 : 1;
      let toGo = '<span class="muted">—</span>';
      if (t.mark != null) {
        const d = (t.entry - t.mark) * sign;             // >0 = still needs to move to entry
        toGo = d <= 0 ? '<span class="pos">● ready</span>'
                      : `${price(Math.abs(d))} <span class="muted">(${(Math.abs(d) / t.mark * 100).toFixed(1)}%)</span>`;
      }
      const acts = usingWorker()
        ? `<button class="mini-btn danger" data-act="delete" data-id="${t.id}" title="Cancel order">✕ Cancel</button>`
        : `<button class="mini-btn go" data-act="trigger" data-id="${t.id}" title="Trigger now (manual fill)">▶ Trigger</button>`
          + `<button class="mini-btn danger" data-act="delete" data-id="${t.id}" title="Cancel order">✕ Cancel</button>`;
      return `<tr>
        ${symCell(t)}${dirCell(t)}
        <td class="num mono">${price(t.level)}</td>
        <td class="num mono entry-col">${price(t.entry)}</td>
        <td class="num mono">${price(t.stop)}</td>
        <td class="num mono">${price(t.target)}</td>
        <td class="num mono">${(t.qty ?? 0).toLocaleString("en-IN")}</td>
        <td class="num mono">${rupee(t.riskRs)}</td>
        <td class="num">${markCell(t)}</td>
        <td class="num mono">${toGo}</td>
        <td class="muted">${shortTime(t.added)}</td>
        <td class="center actions-cell">${acts}</td>
      </tr>`;
    }).join("");
  }

  // OPEN trades (triggered / live positions)
  function renderOpen() {
    const body = $("openBody");
    const rows = state.open.filter((t) => t.state === "TRIGGERED");
    $("openEmpty").classList.toggle("hidden", rows.length > 0);
    body.innerHTML = rows.map((t) => {
      const pnl = openPnl(t, t.mark);
      const acts = `<button class="mini-btn" data-act="close" data-id="${t.id}" title="Close at a price">✕ Close</button>`
        + `<button class="mini-btn danger" data-act="delete" data-id="${t.id}" title="Remove">🗑</button>`;
      return `<tr>
        ${symCell(t)}${dirCell(t)}
        <td class="num mono">${price(t.entry)}</td>
        <td class="num mono">${price(t.stop)}</td>
        <td class="num mono">${price(t.target)}</td>
        <td class="num mono">${(t.qty ?? 0).toLocaleString("en-IN")}</td>
        <td class="num mono">${rupee(t.riskRs)}</td>
        <td class="num">${markCell(t)}</td>
        <td class="num">${pnl == null ? '<span class="muted">—</span>' : pnlSpan(pnl, rupee(Math.abs(pnl)).replace("₹","₹"))}</td>
        <td class="muted">${shortTime(t.triggeredAt)}</td>
        <td class="center actions-cell">${acts}</td>
      </tr>`;
    }).join("");
  }

  function resultPill(r) {
    const map = { TARGET: "res-win", STOP: "res-loss", MANUAL: "res-flat" };
    return `<span class="res-pill ${map[r] || "res-flat"}">${r || "MANUAL"}</span>`;
  }
  function renderClosed() {
    const body = $("closedBody");
    if (!state.closed.length) {
      body.innerHTML = "";
      $("closedEmpty").classList.remove("hidden");
      return;
    }
    $("closedEmpty").classList.add("hidden");
    body.innerHTML = state.closed.map((t) => `<tr>
      <td class="sym"><a href="${tvLink(t.ticker, t.exchange)}" ${tvAttrs}>${esc(t.ticker)}</a></td>
      <td><span class="dir ${t.direction}">${t.direction === "short" ? "Short" : "Long"}</span></td>
      <td class="num mono">${price(t.entry)}</td>
      <td class="num mono">${price(t.exit)}</td>
      <td class="num mono">${(t.qty ?? 0).toLocaleString("en-IN")}</td>
      <td class="num">${pnlSpan(t.pnlRs, rupee(Math.abs(t.pnlRs)).replace("₹","₹"))}</td>
      <td class="num">${pnlSpan(t.pnlPct, `${Math.abs(t.pnlPct).toFixed(1)}%`)}</td>
      <td class="num">${pnlSpan(t.rMultiple, `${t.rMultiple.toFixed(2)}R`)}</td>
      <td>${resultPill(t.result)}</td>
      <td class="muted">${shortTime(t.added)}</td>
      <td class="muted">${shortTime(t.closedAt)}</td>
    </tr>`).join("");
  }

  /* ----------------------------------------------------------- mode pill */
  function setModePill(connected, broker) {
    const pill = $("tradeModePill");
    const detail = $("tradeModeDetail");
    const safety = $("tradeSafetyBadge");
    if (usingWorker()) {
      pill.textContent = connected ? `Worker · ${broker || "paper"}` : "Worker · offline";
      pill.className = "mode-pill " + (connected ? "ok" : "bad");
      detail.textContent = connected ? state.apiUrl : "Trying to reach " + state.apiUrl;
      const live = (broker || "paper").toLowerCase() === "live";
      safety.textContent = live ? "LIVE — real orders" : "PAPER — no real orders";
      safety.className = "badge " + (live ? "live" : "ghost");
    } else {
      pill.textContent = "Local · paper";
      pill.className = "mode-pill";
      detail.textContent = "Trades saved in this browser";
      safety.textContent = "PAPER — no real orders";
      safety.className = "badge ghost";
    }
  }

  /* ----------------------------------------------------------- bindings */
  function bind() {
    // live calc on any form input
    ["fTicker","fExchange","fDirection","fLevel","fBuffer","fSL","fCapital","fRisk","fRR","fNote"]
      .forEach((id) => { const el = $(id); if (el) el.addEventListener("input", renderCalc); });
    // ticker -> custom autocomplete dropdown + auto-pick exchange
    $("fTicker").addEventListener("input", () => { acRender(); autoExchange(); });
    $("fTicker").addEventListener("keydown", acKeys);
    $("fTicker").addEventListener("focus", () => { if ($("fTicker").value) acRender(); });
    $("fTicker").addEventListener("blur", () => setTimeout(acClose, 150));
    $("tickerDD").addEventListener("mousedown", (e) => {
      const it = e.target.closest(".ac-item");
      if (it) { e.preventDefault(); acPick(it.dataset.sym); }
    });

    // submit
    $("tradeForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const inp = readForm();
      if (!inp.ticker || !inp.level || +inp.level <= 0) return;
      // persist capital as the new default
      state.capital = +inp.capital || state.capital;
      localStorage.setItem(LS_CAPITAL, String(state.capital));
      const btn = $("addTradeBtn");
      btn.disabled = true;
      try { await addTrade(inp); $("tradeForm").reset(); $("fCapital").value = state.capital; renderCalc(); }
      catch (err) { alert("Could not add trade: " + err.message); }
      finally { btn.disabled = false; }
    });

    // table action delegation — shared by Pending + Open tables
    const onTableClick = async (e) => {
      const b = e.target.closest("button[data-act]");
      if (!b) return;
      const id = b.dataset.id, act = b.dataset.act;
      if (act === "trigger") return triggerTrade(id);
      if (act === "delete") { if (confirm("Cancel / remove this order?")) return deleteTrade(id); return; }
      if (act === "close") {
        const t = state.open.find((x) => x.id === id);
        const def = t && (t.mark != null ? t.mark : t.entry);
        const v = prompt("Close at what price?", def != null ? def : "");
        if (v == null) return;
        const exit = parseFloat(v);
        if (!exit || exit <= 0) return alert("Enter a valid exit price.");
        if (t && t.state === "WATCHING") { t.state = "TRIGGERED"; t.triggeredAt = new Date().toISOString(); }
        return closeTrade(id, exit);
      }
    };
    const onMarkChange = (e) => {
      const inp = e.target.closest("input.mark-input");
      if (!inp) return;
      const v = parseFloat(inp.value);
      setMark(inp.dataset.id, Number.isNaN(v) ? null : v);
    };
    ["pendingBody", "openBody"].forEach((id) => {
      $(id).addEventListener("click", onTableClick);
      $(id).addEventListener("change", onMarkChange);
    });

    // export closed trades
    $("exportTrades").addEventListener("click", exportClosed);
    $("clearClosed").addEventListener("click", () => {
      if (!state.closed.length) return;
      if (!confirm(`Clear ${state.closed.length} closed trade(s) from history?`)) return;
      if (usingWorker()) { apiSend("/trades/closed", "DELETE").then(refresh).catch(() => {}); }
      else { state.closed = []; saveLocal(); render(); }
    });

    // settings drawer
    $("tradeSettingsBtn").addEventListener("click", () => $("tradeSettings").classList.toggle("hidden"));
    $("apiSaveBtn").addEventListener("click", () => {
      const url = $("apiUrlInput").value.trim().replace(/\/+$/, "");
      state.apiUrl = url;
      if (url) localStorage.setItem(LS_API, url); else localStorage.removeItem(LS_API);
      state.capital = +$("capitalInput").value || state.capital;
      localStorage.setItem(LS_CAPITAL, String(state.capital));
      $("fCapital").value = state.capital;
      $("apiStatus").textContent = url ? "Saved. Connecting…" : "Saved. Local mode.";
      startPolling();
      refresh();
    });
    $("apiTestBtn").addEventListener("click", async () => {
      const url = $("apiUrlInput").value.trim().replace(/\/+$/, "");
      if (!url) { $("apiStatus").textContent = "Enter a URL first."; return; }
      $("apiStatus").textContent = "Testing…";
      try { const r = await fetch(url + "/health", { cache: "no-store" }); $("apiStatus").textContent = r.ok ? "Reachable ✓" : "HTTP " + r.status; }
      catch (e) { $("apiStatus").textContent = "Unreachable ✗"; }
    });
    $("apiClearBtn").addEventListener("click", () => {
      state.apiUrl = ""; localStorage.removeItem(LS_API);
      $("apiUrlInput").value = "";
      $("apiStatus").textContent = "Disconnected. Local mode.";
      stopPolling(); loadLocal(); setModePill(false); render();
    });

    // refresh worker data whenever the Trade tab is opened
    const tradeTab = document.querySelector('.tab[data-tab="trade"]');
    if (tradeTab) tradeTab.addEventListener("click", () => { renderCalc(); refresh(); });
  }

  function exportClosed() {
    const headers = ["ticker","exchange","direction","entry","exit","qty","pnlRs","pnlPct","rMultiple","result","added","closedAt","note"];
    const rows = state.closed.map((t) => headers.map((h) => {
      const v = t[h]; if (v == null) return "";
      if (typeof v === "string" && (v.includes(",") || v.includes('"'))) return `"${v.replace(/"/g, '""')}"`;
      return v;
    }).join(","));
    const csv = [headers.join(",")].concat(rows).join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    a.download = `phenom_trades_${new Date().toISOString().slice(0,10)}.csv`;
    a.click(); URL.revokeObjectURL(a.href);
  }

  /* ----------------------------------------------------------- polling */
  function startPolling() { stopPolling(); if (usingWorker()) state.pollTimer = setInterval(refresh, POLL_MS); }
  function stopPolling() { if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; } }

  /* ----------------------------------------------------------- init */
  function init() {
    // hydrate settings inputs
    $("apiUrlInput").value = state.apiUrl;
    $("capitalInput").value = state.capital;
    $("fCapital").value = state.capital;

    setModePill(false);
    loadAnn();
    loadLocal();
    loadTickers();
    render();
    renderCalc();
    bind();

    if (usingWorker()) { startPolling(); refresh(); }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
