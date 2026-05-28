/* ============================================================================
   Phenom News & Catalysts
   Reads data/news.json (built daily by the "Fetch news" GitHub Action — and on
   demand when you click "Fetch fresh") and lets you search any scanner stock for
   its latest headlines. Pure static read — no API keys, no CORS, nothing secret.
   ========================================================================== */
(() => {
  "use strict";
  const URL = "../data/news.json";          // -> data/news.json on Pages
  const $ = (id) => document.getElementById(id);
  const state = { data: null, loaded: false };

  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function timeAgo(iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso || "";
    const mins = Math.round((Date.now() - d.getTime()) / 60000);
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.round(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.round(hrs / 24);
    return `${days}d ago`;
  }

  async function load() {
    try {
      const r = await fetch(URL + "?t=" + Date.now(), { cache: "no-store" });
      if (!r.ok) throw new Error("HTTP " + r.status);
      state.data = await r.json();
      state.loaded = true;
      const gen = state.data.generated_at ? new Date(state.data.generated_at) : null;
      $("newsAsOf").textContent = gen ? "as of " + gen.toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" }) : "";
      // populate the datalist with symbols that have news
      const dl = $("newsSymbols");
      const syms = Object.keys(state.data.stocks || {}).sort();
      dl.innerHTML = syms.map((s) => `<option value="${esc(s)}">`).join("");
      $("newsAsOf").title = `${syms.length} stocks with cached headlines`;
    } catch (e) {
      $("newsAsOf").textContent = "no news file yet";
      $("newsEmpty").innerHTML = `No <code>news.json</code> yet — run the “Fetch fresh” workflow once, then refresh.`;
    }
  }

  function render(symRaw) {
    const sym = (symRaw || "").trim().toUpperCase();
    const result = $("newsResult");
    if (!sym) { result.innerHTML = ""; $("newsEmpty").classList.remove("hidden"); return; }
    if (!state.data) { return; }
    $("newsEmpty").classList.add("hidden");

    const items = (state.data.stocks || {})[sym];
    const meta = (state.data.names || {})[sym] || {};
    const name = meta.name || sym;

    if (!items || !items.length) {
      result.innerHTML = `<div class="news-card"><div class="news-head"><h3>${esc(sym)}</h3></div>
        <p class="muted" style="padding:8px 0">No cached headlines for “${esc(sym)}”. It may not be a current qualifier —
        try a name from the Scanner, or click “Fetch fresh” to refresh the feed.</p></div>`;
      return;
    }
    const rows = items.map((h) => `
      <a class="news-item" href="${esc(h.link)}" target="_blank" rel="noopener">
        <span class="news-title">${esc(h.title)}</span>
        <span class="news-meta"><span class="news-src">${esc(h.source || "")}</span>${h.date ? ` · ${esc(timeAgo(h.date))}` : ""}</span>
      </a>`).join("");
    result.innerHTML = `<div class="news-card">
        <div class="news-head"><h3>${esc(name)} <span class="news-sym">${esc(sym)}</span></h3>
          <span class="muted">${items.length} headlines</span></div>
        ${rows}
      </div>`;
  }

  function bind() {
    $("newsSearch").addEventListener("input", (e) => render(e.target.value));
    $("newsReload").addEventListener("click", async () => {
      $("newsReload").textContent = "…";
      await load();
      render($("newsSearch").value);
      $("newsReload").textContent = "Refresh";
    });
    const tab = document.querySelector('.tab[data-tab="news"]');
    if (tab) tab.addEventListener("click", () => { if (!state.loaded) load(); });
  }

  function init() { bind(); load(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
