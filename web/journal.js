/* ============================================================================
   Phenom Trade Journal
   Reads every trade from the Trade desk (window.PhenomTrades) and logs the full
   record: the plan, the outcome, a timeline of what happened, and your own
   journaling (setup, grade, what went right/wrong, lessons, tags, notes).
   Journaling auto-saves and works in both LOCAL and WORKER modes.
   ========================================================================== */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const jstate = { expanded: new Set(), q: "", filter: "all" };

  const GRADES = ["", "A", "B", "C", "D", "F"];
  const EMOTIONS = ["", "Calm", "Confident", "FOMO", "Fearful", "Greedy", "Revenge", "Bored"];

  /* ---------- formatters ---------- */
  const rupee = (v) => v == null || Number.isNaN(v) ? "—" : "₹" + Number(v).toLocaleString("en-IN", { maximumFractionDigits: 0 });
  const price = (v) => v == null || Number.isNaN(v) ? "—" : Number(v).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const pnlSpan = (v, txt) => {
    if (v == null || Number.isNaN(v)) return `<span class="muted">—</span>`;
    const cls = v > 0 ? "pos" : v < 0 ? "neg" : "muted";
    return `<span class="${cls}">${v > 0 ? "+" : ""}${txt}</span>`;
  };
  const dt = (iso) => {
    if (!iso) return "—";
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString("en-IN", { day: "2-digit", month: "short", year: "2-digit", hour: "2-digit", minute: "2-digit" });
  };
  const dur = (a, b) => {
    if (!a || !b) return "—";
    let ms = new Date(b) - new Date(a);
    if (!(ms >= 0)) return "—";
    const d = Math.floor(ms / 86400000); ms -= d * 86400000;
    const h = Math.floor(ms / 3600000); ms -= h * 3600000;
    const m = Math.floor(ms / 60000);
    if (d) return `${d}d ${h}h`;
    if (h) return `${h}h ${m}m`;
    return `${m}m`;
  };

  const IS_MOBILE = /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent);
  const tvLink = (sym, exch) => {
    const e = exch === "BSE" ? "BSE" : "NSE";
    return IS_MOBILE ? `https://www.tradingview.com/symbols/${e}-${encodeURIComponent(sym)}/`
                     : `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(e + ":" + sym)}`;
  };
  const tvAttrs = IS_MOBILE ? `rel="noopener"` : `target="_blank" rel="noopener"`;

  /* ---------- data ---------- */
  function allTrades() {
    const api = window.PhenomTrades;
    if (!api) return [];
    const d = api.getAll();
    // open first (most actionable), then closed; each newest-first already
    const open = (d.open || []).map((t) => ({ ...t, _open: true }));
    const closed = (d.closed || []).map((t) => ({ ...t, _open: false }));
    return [...open, ...closed];
  }

  function matches(t) {
    if (jstate.filter === "open" && !t._open) return false;
    if (jstate.filter === "closed" && t._open) return false;
    if (jstate.filter === "win" && !((t.pnlRs ?? 0) > 0)) return false;
    if (jstate.filter === "loss" && !((t.pnlRs ?? 0) < 0)) return false;
    const q = jstate.q.trim().toLowerCase();
    if (q) {
      const a = t._ann || {};
      const hay = [t.ticker, a.setup, a.tags, a.lesson, a.wentRight, a.wentWrong,
        ...(a.notes || []).map((n) => n.text)].join(" ").toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  }

  /* ---------- stats ---------- */
  function renderStats() {
    const trades = allTrades();
    const closed = trades.filter((t) => !t._open);
    const cells = [];
    const add = (label, value, klass) => cells.push(`<div class="tstat"><div class="tstat-v mono ${klass || ""}">${value}</div><div class="tstat-l">${label}</div></div>`);

    const wins = closed.filter((t) => (t.pnlRs ?? 0) > 0);
    const losses = closed.filter((t) => (t.pnlRs ?? 0) < 0);
    const winRate = closed.length ? Math.round(wins.length / closed.length * 100) : null;
    const net = closed.reduce((s, t) => s + (t.pnlRs ?? 0), 0);
    const rs = closed.map((t) => t.rMultiple).filter((v) => v != null && !Number.isNaN(v));
    const expectancy = rs.length ? rs.reduce((a, b) => a + b, 0) / rs.length : null;
    const grossWin = wins.reduce((s, t) => s + (t.pnlRs ?? 0), 0);
    const grossLoss = Math.abs(losses.reduce((s, t) => s + (t.pnlRs ?? 0), 0));
    const pf = grossLoss > 0 ? grossWin / grossLoss : (grossWin > 0 ? Infinity : null);
    const avgWinR = wins.length ? wins.reduce((s, t) => s + (t.rMultiple ?? 0), 0) / wins.length : null;
    const avgLossR = losses.length ? losses.reduce((s, t) => s + (t.rMultiple ?? 0), 0) / losses.length : null;
    const holds = closed.map((t) => (t.triggeredAt && t.closedAt) ? (new Date(t.closedAt) - new Date(t.triggeredAt)) : null).filter((v) => v != null && v >= 0);
    const avgHoldMs = holds.length ? holds.reduce((a, b) => a + b, 0) / holds.length : null;

    add("Total", trades.length);
    add("Closed", closed.length);
    add("Win rate", winRate == null ? "—" : winRate + "%", winRate != null && winRate >= 50 ? "pos" : winRate != null ? "neg" : "");
    add("Expectancy", expectancy == null ? "—" : `${expectancy > 0 ? "+" : ""}${expectancy.toFixed(2)}R`, expectancy == null ? "" : expectancy > 0 ? "pos" : "neg");
    add("Net P&L", closed.length ? (net >= 0 ? "+" : "−") + rupee(Math.abs(net)).slice(1) : "—", net > 0 ? "pos" : net < 0 ? "neg" : "");
    add("Profit factor", pf == null ? "—" : (pf === Infinity ? "∞" : pf.toFixed(2)), pf != null && pf >= 1 ? "pos" : pf != null ? "neg" : "");
    add("Avg win", avgWinR == null ? "—" : `+${avgWinR.toFixed(2)}R`, "pos");
    add("Avg loss", avgLossR == null ? "—" : `${avgLossR.toFixed(2)}R`, "neg");
    add("Avg hold", avgHoldMs == null ? "—" : dur(0, avgHoldMs));
    $("journalStats").innerHTML = cells.join("");
  }

  /* ---------- per-trade card ---------- */
  function field(id, label, val, type) {
    if (type === "grade")
      return selectField(id, label, val, GRADES);
    if (type === "emotion")
      return selectField(id, label, val, EMOTIONS);
    if (type === "area")
      return `<label class="jf jf-wide"><span class="jf-l">${label}</span><textarea data-jf="${id}" rows="2" placeholder="…">${esc(val || "")}</textarea></label>`;
    return `<label class="jf"><span class="jf-l">${label}</span><input type="text" data-jf="${id}" value="${esc(val || "")}" placeholder="…" /></label>`;
  }
  function selectField(id, label, val, opts) {
    const o = opts.map((x) => `<option value="${esc(x)}"${x === (val || "") ? " selected" : ""}>${x || "—"}</option>`).join("");
    return `<label class="jf"><span class="jf-l">${label}</span><select data-jf="${id}">${o}</select></label>`;
  }

  function timeline(t) {
    const icon = { CREATED: "○", TRIGGERED: "▶", NOTE: "✎", CLOSED: "●" };
    return (t._events || []).map((e) =>
      `<li class="tl-item tl-${(e.type || "").toLowerCase()}"><span class="tl-dot">${icon[e.type] || "·"}</span>
        <span class="tl-time mono">${dt(e.ts)}</span>
        <span class="tl-type">${esc(e.type || "")}</span>
        ${e.note ? `<span class="tl-note">${esc(e.note)}</span>` : ""}</li>`).join("");
  }

  function card(t) {
    const a = t._ann || {};
    const open = jstate.expanded.has(t.id);
    const dirCls = t.direction === "short" ? "short" : "long";
    const statePill = t._open
      ? `<span class="trade-state ${t.state === "TRIGGERED" ? "st-live" : "st-watch"}">${t.state}</span>`
      : `<span class="res-pill ${t.result === "TARGET" ? "res-win" : t.result === "STOP" ? "res-loss" : "res-flat"}">${t.result || "CLOSED"}</span>`;
    const pnl = t._open ? "" : pnlSpan(t.pnlRs, rupee(Math.abs(t.pnlRs)).slice(0));
    const r = t._open ? "" : pnlSpan(t.rMultiple, `${Math.abs(t.rMultiple).toFixed(2)}R`);
    const grade = a.grade ? `<span class="grade-pill g-${a.grade}">${a.grade}</span>` : "";

    const summary = `<div class="jcard-head" data-toggle="${t.id}">
      <span class="jc-caret">${open ? "▾" : "▸"}</span>
      <span class="sym"><a href="${tvLink(t.ticker, t.exchange)}" ${tvAttrs} onclick="event.stopPropagation()">${esc(t.ticker)}</a></span>
      <span class="dir ${dirCls}">${t.direction === "short" ? "Short" : "Long"}</span>
      ${statePill}
      ${a.setup ? `<span class="jc-setup">${esc(a.setup)}</span>` : ""}
      ${grade}
      <span class="jc-spacer"></span>
      <span class="jc-pnl">${pnl} ${r}</span>
      <span class="jc-date muted">${dt(t.added)}</span>
    </div>`;

    if (!open) return `<article class="jcard">${summary}</article>`;

    const plan = `
      <div class="jblock"><h4>Plan</h4>
        <div class="jgrid">
          ${kv("Level", price(t.level))} ${kv("Buffer", (t.buffer ?? 1) + "%")}
          ${kv("Entry", price(t.entry))} ${kv("Stop", price(t.stop))}
          ${kv("Target", price(t.target) + ` (${t.rr ?? 3}R)`)} ${kv("Qty", (t.qty ?? 0).toLocaleString("en-IN"))}
          ${kv("Capital", rupee(t.capital))} ${kv("Risk %", (t.riskPct ?? "—") + "%")}
          ${kv("Risk ₹", rupee(t.riskRs))} ${kv("Risk/share", price(t.riskPerShare))}
        </div>
      </div>`;

    const outcome = t._open ? "" : `
      <div class="jblock"><h4>Outcome</h4>
        <div class="jgrid">
          ${kv("Exit", price(t.exit))} ${kv("P&L", pnlSpan(t.pnlRs, rupee(Math.abs(t.pnlRs)).slice(0)))}
          ${kv("P&L %", pnlSpan(t.pnlPct, Math.abs(t.pnlPct).toFixed(1) + "%"))} ${kv("R", pnlSpan(t.rMultiple, Math.abs(t.rMultiple).toFixed(2) + "R"))}
          ${kv("Result", t.result || "—")} ${kv("Hold", dur(t.triggeredAt, t.closedAt))}
        </div>
      </div>`;

    const journal = `
      <div class="jblock"><h4>Journal</h4>
        <div class="jfields">
          ${field("setup", "Setup / strategy", a.setup)}
          ${field("grade", "Grade", a.grade, "grade")}
          ${field("emotion", "Emotion", a.emotion, "emotion")}
          ${field("tags", "Tags", a.tags)}
          ${field("wentRight", "What went right", a.wentRight, "area")}
          ${field("wentWrong", "What went wrong", a.wentWrong, "area")}
          ${field("lesson", "Lesson", a.lesson, "area")}
        </div>
      </div>`;

    const notes = `
      <div class="jblock"><h4>Notes</h4>
        <ul class="jnotes">${(a.notes || []).map((n) => `<li><span class="jn-time mono">${dt(n.ts)}</span> ${esc(n.text)}</li>`).join("") || '<li class="muted">No notes yet.</li>'}</ul>
        <div class="jnote-add">
          <input type="text" data-note="${t.id}" placeholder="Add a note…" />
          <button class="btn-ghost" data-addnote="${t.id}">Add</button>
        </div>
      </div>`;

    const tl = `<div class="jblock"><h4>Timeline</h4><ul class="timeline">${timeline(t) || '<li class="muted">—</li>'}</ul></div>`;

    return `<article class="jcard is-open">${summary}<div class="jcard-body">${plan}${outcome}${journal}${tl}${notes}</div></article>`;
  }
  const kv = (k, v) => `<div class="kv"><span class="kv-k">${k}</span><span class="kv-v mono">${v}</span></div>`;

  /* ---------- render ---------- */
  function render() {
    if (!$("journalList")) return;
    renderStats();
    const trades = allTrades().filter(matches);
    if (!trades.length) {
      $("journalList").innerHTML = "";
      $("journalEmpty").classList.remove("hidden");
    } else {
      $("journalEmpty").classList.add("hidden");
      $("journalList").innerHTML = trades.map(card).join("");
    }
  }

  /* ---------- bindings ---------- */
  function bind() {
    const list = $("journalList");

    list.addEventListener("click", (e) => {
      const head = e.target.closest("[data-toggle]");
      if (head) {
        const id = head.dataset.toggle;
        if (jstate.expanded.has(id)) jstate.expanded.delete(id); else jstate.expanded.add(id);
        render();
        return;
      }
      const addBtn = e.target.closest("[data-addnote]");
      if (addBtn) {
        const id = addBtn.dataset.addnote;
        const inp = list.querySelector(`input[data-note="${id}"]`);
        if (inp && inp.value.trim()) { window.PhenomTrades.addNote(id, inp.value); }
        return;
      }
    });

    // auto-save journal fields on change (blur) — find the owning card's id
    list.addEventListener("change", (e) => {
      const el = e.target.closest("[data-jf]");
      if (!el) return;
      const cardEl = el.closest(".jcard");
      const id = cardEl?.querySelector("[data-toggle]")?.dataset.toggle;
      if (id) window.PhenomTrades.annotate(id, { [el.dataset.jf]: el.value });
    });
    // add note on Enter
    list.addEventListener("keydown", (e) => {
      const inp = e.target.closest("input[data-note]");
      if (inp && e.key === "Enter") { e.preventDefault(); if (inp.value.trim()) window.PhenomTrades.addNote(inp.dataset.note, inp.value); }
    });

    $("journalSearch").addEventListener("input", (e) => { jstate.q = e.target.value; render(); });
    $("journalFilter").addEventListener("change", (e) => { jstate.filter = e.target.value; render(); });
    $("exportJournal").addEventListener("click", exportJournal);

    const tab = document.querySelector('.tab[data-tab="journal"]');
    if (tab) tab.addEventListener("click", render);
  }

  function exportJournal() {
    const rows = allTrades();
    const headers = ["ticker","exchange","direction","state","level","entry","stop","target","qty","capital","riskPct","riskRs","rr","exit","pnlRs","pnlPct","rMultiple","result","added","triggeredAt","closedAt","setup","grade","emotion","tags","wentRight","wentWrong","lesson","notes"];
    const line = (t) => headers.map((h) => {
      let v;
      if (["setup","grade","emotion","tags","wentRight","wentWrong","lesson"].includes(h)) v = (t._ann || {})[h];
      else if (h === "notes") v = ((t._ann || {}).notes || []).map((n) => `${n.ts} ${n.text}`).join(" | ");
      else if (h === "state") v = t._open ? t.state : "CLOSED";
      else v = t[h];
      if (v == null) return "";
      v = String(v);
      return (v.includes(",") || v.includes('"') || v.includes("\n")) ? `"${v.replace(/"/g, '""')}"` : v;
    }).join(",");
    const csv = [headers.join(",")].concat(rows.map(line)).join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    a.download = `phenom_journal_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click(); URL.revokeObjectURL(a.href);
  }

  /* ---------- init (wait for PhenomTrades from trade.js) ---------- */
  function init() {
    if (!window.PhenomTrades) { setTimeout(init, 60); return; }
    bind();
    window.PhenomTrades.subscribe(render);
    render();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
