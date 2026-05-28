/* ============================================================================
   Phenom hero animation — a live, self-generating candlestick stream that
   behaves like a real chart: trends, sharp pullbacks and ranges, with 3 moving
   averages laid over the candles, a pulsing last-price marker, rising "spark"
   particles off strong up candles, and a scrolling ticker tape underneath.
   Pure canvas, no deps. Pauses when the tab is hidden / off the Scanner page.
   ========================================================================== */
(() => {
  "use strict";
  const canvas = document.getElementById("heroAnim");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");

  const UP = "#5fd39b", DOWN = "#ff5566";
  const GRID = "rgba(236,222,226,0.045)";
  const RED = (a) => `rgba(225,29,42,${a})`;
  // moving averages (period, color, width)
  const MAS = [
    { p: 7,  c: "rgba(255,193,77,0.95)", w: 1.3 },   // fast — amber
    { p: 21, c: "rgba(201,194,200,0.85)", w: 1.3 },  // medium — silver
    { p: 45, c: "rgba(255,122,138,0.85)", w: 1.6 },  // slow — soft red
  ];

  const MAXC = 60;
  let W = 0, H = 0, DPR = 1;
  let candles = [];                // {o,h,l,c}
  let price = 100, mom = 0.4, vol = 1.3;
  let forming = null, formT = 0, formLen = 12;
  let sparks = [];
  let labelPrice = 100;
  let t = 0, raf = null;

  /* ---------- sizing ---------- */
  function resize() {
    const r = canvas.getBoundingClientRect();
    if (!r.width || !r.height) return;
    DPR = Math.min(2, window.devicePixelRatio || 1);
    W = r.width; H = r.height;
    canvas.width = Math.round(W * DPR);
    canvas.height = Math.round(H * DPR);
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  }

  /* ---------- price model: regimes give trends + deeper pullbacks + ranges --- */
  function tickPrice() {
    if (Math.random() < 0.016) {                 // occasional regime change
      const r = Math.random();
      if (r < 0.50) mom = 0.45 + Math.random() * 0.75;        // uptrend (conviction)
      else if (r < 0.82) mom = -(0.8 + Math.random() * 1.7);  // sharp / deep pullback
      else mom = (Math.random() - 0.5) * 0.35;                // range / chop
      vol = 0.7 + Math.random() * 2.1;
    }
    price += mom + (Math.random() - 0.5) * vol * 1.9;
    if (price < 28) { price = 28 + Math.random() * 4; mom = Math.abs(mom) + 0.35; }   // bounce off floor
    if (price > 330) { mom = -Math.abs(mom) - 0.2; }                                   // reversal off ceiling
  }
  function startCandle() { forming = { o: price, h: price, l: price, c: price }; formT = 0; formLen = 8 + (Math.random() * 12 | 0); }
  function finalize() {
    candles.push(forming);
    if (candles.length > MAXC) candles.shift();
    if (forming.c > forming.o && (forming.c - forming.o) > vol * 0.6) spawnSparks();
    startCandle();
  }
  function seed() {
    candles = []; price = 100; mom = 0.5; startCandle();
    for (let i = 0; i < MAXC; i++) {
      for (let k = 0; k < 6; k++) { tickPrice(); forming.c = price; forming.h = Math.max(forming.h, price); forming.l = Math.min(forming.l, price); }
      candles.push(forming); startCandle();
    }
    labelPrice = price;
  }
  function spawnSparks() {
    const n = 3 + (Math.random() * 4 | 0);
    for (let i = 0; i < n; i++) sparks.push({ dx: (Math.random() - 0.5) * 7, y: 0, vy: 0.6 + Math.random() * 1.2, life: 1, r: 0.8 + Math.random() * 1.6 });
    if (sparks.length > 60) sparks.splice(0, sparks.length - 60);
  }

  /* ---------- moving average over candle closes (partial window at the start) */
  function maSeries(period, arr) {
    const out = new Array(arr.length); let sum = 0;
    for (let i = 0; i < arr.length; i++) {
      sum += arr[i];
      if (i >= period) sum -= arr[i - period];
      out[i] = sum / Math.min(i + 1, period);
    }
    return out;
  }

  /* ---------- render ---------- */
  function draw() {
    const padL = 8, padR = 64, padT = 16, padB = 30;
    const plotW = W - padL - padR, plotH = H - padT - padB;
    const all = forming ? candles.concat(forming) : candles.slice();
    const n = all.length;
    const step = plotW / (MAXC + 1);
    const cw = Math.max(2, step * 0.6);

    let lo = Infinity, hi = -Infinity;
    for (const c of all) { if (c.l < lo) lo = c.l; if (c.h > hi) hi = c.h; }
    const pad = (hi - lo) * 0.10 + 1; lo -= pad; hi += pad;
    const yOf = (p) => padT + plotH - ((p - lo) / (hi - lo || 1)) * plotH;
    const xOf = (i) => padL + (i + (MAXC + 1 - n) + 0.5) * step;

    ctx.clearRect(0, 0, W, H);

    // grid
    ctx.strokeStyle = GRID; ctx.lineWidth = 1;
    for (let g = 0; g <= 4; g++) { const y = padT + (plotH * g) / 4; ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke(); }

    // candles
    for (let i = 0; i < n; i++) {
      const c = all[i], x = xOf(i);
      const col = c.c >= c.o ? UP : DOWN, isLast = i === n - 1;
      ctx.strokeStyle = col; ctx.fillStyle = col;
      ctx.globalAlpha = isLast ? 1 : 0.9;
      ctx.shadowBlur = isLast ? 12 : 0; if (isLast) ctx.shadowColor = col;
      ctx.lineWidth = 1.1; ctx.beginPath(); ctx.moveTo(x, yOf(c.h)); ctx.lineTo(x, yOf(c.l)); ctx.stroke();
      const yo = yOf(c.o), yc = yOf(c.c), top = Math.min(yo, yc), bh = Math.max(1.4, Math.abs(yc - yo));
      ctx.fillRect(x - cw / 2, top, cw, bh);
    }
    ctx.shadowBlur = 0; ctx.globalAlpha = 1;

    // moving averages
    const closes = all.map((c) => c.c);
    for (const ma of MAS) {
      const s = maSeries(ma.p, closes);
      ctx.strokeStyle = ma.c; ctx.lineWidth = ma.w; ctx.lineJoin = "round";
      ctx.beginPath();
      for (let i = ma.p - 1 < 0 ? 0 : 0; i < n; i++) { const x = xOf(i), y = yOf(s[i]); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }
      ctx.stroke();
    }

    // last-price marker
    const last = all[n - 1], lx = xOf(n - 1), ly = yOf(last.c);
    labelPrice += (last.c - labelPrice) * 0.2;
    ctx.strokeStyle = RED(0.32); ctx.setLineDash([4, 5]); ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(padL, ly); ctx.lineTo(W - padR, ly); ctx.stroke(); ctx.setLineDash([]);
    const pulse = 4 + Math.sin(t / 14) * 1.6;
    ctx.fillStyle = RED(0.16); ctx.beginPath(); ctx.arc(lx, ly, pulse + 6, 0, 7); ctx.fill();
    ctx.fillStyle = "#fff"; ctx.shadowColor = "#ff3b46"; ctx.shadowBlur = 14; ctx.beginPath(); ctx.arc(lx, ly, 3.2, 0, 7); ctx.fill(); ctx.shadowBlur = 0;
    ctx.fillStyle = last.c >= last.o ? UP : DOWN;
    ctx.font = "600 13px 'IBM Plex Mono', monospace"; ctx.textAlign = "left"; ctx.textBaseline = "middle";
    ctx.fillText(labelPrice.toFixed(2), Math.min(lx + 10, W - padR + 6), ly);

    // sparks rise from the last (up) candle
    for (const s of sparks) {
      ctx.globalAlpha = Math.max(0, s.life);
      ctx.fillStyle = UP; ctx.shadowColor = UP; ctx.shadowBlur = 8;
      ctx.beginPath(); ctx.arc(lx + s.dx, ly - s.y, s.r, 0, 7); ctx.fill();
    }
    ctx.globalAlpha = 1; ctx.shadowBlur = 0;
  }

  function update() {
    tickPrice();
    forming.c = price; forming.h = Math.max(forming.h, price); forming.l = Math.min(forming.l, price);
    if (++formT >= formLen) finalize();
    for (const s of sparks) { s.y += s.vy; s.vy *= 0.99; s.life -= 0.012; }
    sparks = sparks.filter((s) => s.life > 0);
  }

  /* ---------- loop (~30fps; pauses when hidden / off Scanner) ---------- */
  let acc = 0, prev = 0;
  function frame(ts) {
    raf = requestAnimationFrame(frame);
    const onScanner = document.querySelector('.page[data-page="scanner"]')?.classList.contains("is-active");
    if (document.hidden || !onScanner || !W) { prev = ts; return; }
    const dt = ts - prev; prev = ts; acc += dt; t++;
    if (acc < 33) return; acc = 0;
    update(); draw();
  }

  /* ---------- ticker tape ---------- */
  function buildTape() {
    const tape = document.getElementById("heroTape");
    if (!tape) return;
    const syms = [["RELIANCE",2934],["TCS",4112],["HDFCBANK",1678],["CGPOWER",934],["INFY",1856],
      ["ICICIBANK",1244],["DIXON",14250],["TATAMOTORS",982],["SBIN",842],["BHARTIARTL",1620],
      ["LT",3680],["ADANIENT",2410],["DREDGECORP",1191],["IFCI",68],["SOUTHWEST",269]];
    const item = ([s, p]) => {
      const ch = Math.random() * 6 - 2, cls = ch >= 0 ? "pos" : "neg";
      const v = (p * (1 + ch / 100)).toFixed(p > 1000 ? 0 : 2);
      return `<span class="tk"><b>${s}</b><span>${v}</span><span class="${cls}">${ch >= 0 ? "▲" : "▼"}${Math.abs(ch).toFixed(2)}%</span></span>`;
    };
    tape.innerHTML = `<div class="hero-tape-track">${syms.concat(syms).map(item).join("")}</div>`;
  }

  /* ---------- init ---------- */
  function init() {
    resize(); seed(); buildTape();
    if (window.ResizeObserver) new ResizeObserver(() => { resize(); draw(); }).observe(canvas.parentElement);
    window.addEventListener("resize", () => { resize(); draw(); });
    draw();
    raf = requestAnimationFrame(frame);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
