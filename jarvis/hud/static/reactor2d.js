/* J.A.R.V.I.S. instrument in 2D: used only if 3D (WebGL2) isn't available on this Mac.
   Draws the same artwork as scene.js (from art.js), flat, and reads the same window.HUD_SCENE state. */
"use strict";

window.startReactor2D = function startReactor2D(canvas) {
  const S = window.HUD_SCENE;
  const ART = window.JARVIS_ART;
  const { TAU, LOOKS, LAYERS } = ART;
  const ctx = canvas.getContext("2d");
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const color = [...LOOKS.boot.color];
  const live = { level: 0.7, spin: 0.6, sweep: 0.2, activity: 0, audio: 0, warn: 0 };
  const layers = LAYERS.map((spec) => ({ spec, canvas: null, angle: spec.spin ? Math.random() * TAU : 0 }));
  const buffer = document.createElement("canvas");     // the layers are combined here, then tinted
  const bctx = buffer.getContext("2d");
  let readoutsKey = "", last = performance.now(), sweep = 0, act = 0, lastReadouts = 0;

  function paintAll() {
    readoutsKey = JSON.stringify(S.readouts || {});
    layers.forEach((l) => { l.canvas = ART.paint(l.spec, S.readouts, null, 0.5); });
  }
  paintAll();

  function resize() {
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.round(canvas.clientWidth * dpr));
    canvas.height = Math.max(1, Math.round(canvas.clientHeight * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  window.addEventListener("resize", resize);
  resize();

  function geometry() {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    const v = S.view;                          // the free space between the panels (from hud.js)
    if (v && v.w > 50 && v.h > 50) return { cx: v.x, cy: v.y, R: Math.min(v.w, v.h) * 0.47 / 1.08 };
    return { cx: w / 2, cy: h * 0.46, R: Math.min(w, h) * 0.42 };
  }
  canvas.addEventListener("click", (ev) => {
    const { cx, cy, R } = geometry();
    if (Math.hypot(ev.clientX - cx, ev.clientY - cy) < R * 0.34) window.dispatchEvent(new CustomEvent("jarvis-core-click"));
  });

  const rgba = (a) => `rgba(${Math.round(color[0] * 255)},${Math.round(color[1] * 255)},${Math.round(color[2] * 255)},${a})`;
  function ringArc(cx, cy, r, from, length, width, alpha, dash) {
    ctx.save();
    ctx.lineWidth = width;
    ctx.strokeStyle = rgba(alpha);
    if (dash) ctx.setLineDash(dash);
    ctx.beginPath();
    ctx.arc(cx, cy, r, from - Math.PI / 2, from + length - Math.PI / 2);
    ctx.stroke();
    ctx.restore();
  }

  function draw(now) {
    requestAnimationFrame(draw);
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    const t = now / 1000;
    const motion = reduceMotion ? 0.25 : 1;
    if (now - lastReadouts > 500) {
      lastReadouts = now;
      const key = JSON.stringify(S.readouts || {});
      if (key !== readoutsKey) {
        readoutsKey = key;
        layers.forEach((l) => { if (l.spec.live) ART.paint(l.spec, S.readouts, l.canvas, 0.5); });
      }
    }
    const look = LOOKS[S.state] || LOOKS.standby;
    const k = Math.min(1, dt * 2.5);
    for (let i = 0; i < 3; i++) color[i] += (look.color[i] - color[i]) * k;
    live.level += (look.level - live.level) * k;
    live.spin += (look.spin - live.spin) * k;
    live.sweep += (look.sweep - live.sweep) * k;
    live.activity += (look.activity - live.activity) * Math.min(1, dt * 4);
    live.warn += ((S.state === "confirm" ? 1 : 0) - live.warn) * Math.min(1, dt * 4);
    const raw = S.state === "speaking" ? S.levels.out : (S.state === "listening" || S.state === "confirm") ? S.levels.mic : 0;
    const audio = Math.min(1, Math.sqrt(Math.max(0, raw || 0)) * 2.4);
    live.audio += (audio - live.audio) * Math.min(1, dt * (audio > live.audio ? 14 : 4));
    const A = live.audio;
    const bright = live.level * (S.state === "boot" ? 0.35 + 0.65 * Math.max(0.08, S.boot || 0) : 1);

    const w = canvas.clientWidth, h = canvas.clientHeight;
    const { cx, cy, R } = geometry();
    ctx.globalCompositeOperation = "source-over";
    ctx.fillStyle = "#020507";
    ctx.fillRect(0, 0, w, h);

    // glass layers: combine them in white, then colour the result with the state's tint
    const dpr = window.devicePixelRatio || 1;
    const size = Math.ceil(R * 2.3 * 2 * dpr);
    if (buffer.width !== size) { buffer.width = buffer.height = size; }
    bctx.setTransform(1, 0, 0, 1, 0, 0);
    bctx.globalCompositeOperation = "source-over";
    bctx.clearRect(0, 0, size, size);
    bctx.globalCompositeOperation = "lighter";
    layers.forEach((l) => {
      l.angle += dt * 0.035 * l.spec.spin * live.spin * motion;
      const px = l.spec.radius * R * dpr * 2;
      bctx.setTransform(1, 0, 0, 1, size / 2, size / 2);
      bctx.rotate(l.angle);
      bctx.globalAlpha = Math.min(1, l.spec.opacity * bright);
      bctx.drawImage(l.canvas, -px / 2, -px / 2, px, px);
    });
    bctx.setTransform(1, 0, 0, 1, 0, 0);
    bctx.globalAlpha = 1;
    bctx.globalCompositeOperation = "source-in";
    bctx.fillStyle = rgba(1);
    bctx.fillRect(0, 0, size, size);
    ctx.globalCompositeOperation = "lighter";
    ctx.drawImage(buffer, cx - size / dpr / 2, cy - size / dpr / 2, size / dpr, size / dpr);

    // spectrum and oscilloscope
    ctx.lineWidth = Math.max(1, R * 0.0032);
    ctx.strokeStyle = rgba((0.4 + 0.55 * Math.min(1, A * 1.4)) * bright);
    ctx.beginPath();
    for (let i = 0; i < 160; i++) {
      const a = (i / 160) * TAU;
      const n = 0.5 + 0.5 * Math.sin(i * 0.37 + t * 6.0) * Math.sin(i * 0.11 - t * 2.7);
      const len = 0.007 + A * (0.018 + 0.09 * n * n);
      ctx.moveTo(cx + Math.sin(a) * R * 0.3, cy - Math.cos(a) * R * 0.3);
      ctx.lineTo(cx + Math.sin(a) * R * (0.3 + len), cy - Math.cos(a) * R * (0.3 + len));
    }
    ctx.stroke();
    ctx.beginPath();
    for (let i = 0; i <= 256; i++) {
      const a = (i / 256) * TAU;
      const r = R * (0.255 + A * (0.016 * Math.sin(a * 9 + t * 9) * Math.sin(a * 3 - t * 4) + 0.006 * Math.sin(a * 23 + t * 15)));
      const x = cx + Math.sin(a) * r, y = cy - Math.cos(a) * r;
      if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y);
    }
    ctx.strokeStyle = rgba((0.45 + 0.45 * Math.min(1, A * 1.5)) * bright);
    ctx.stroke();

    // working arc, start-up arc, authorisation ring
    act = (act + dt * 2.2 * motion) % TAU;
    sweep = (sweep + dt * (0.5 + 1.4 * live.sweep) * motion) % TAU;
    if (live.activity > 0.01) ringArc(cx, cy, R * 0.568, act, 0.7 + 0.45 * Math.sin(t * 1.7), R * 0.007, live.activity * 0.9 * bright);
    if (live.sweep > 0.01) {
      for (let i = 0; i < 24; i++) {
        const a = sweep - i * 0.03;
        ctx.strokeStyle = rgba(live.sweep * 0.5 * bright * (i ? 0.5 * (1 - i / 24) ** 3 : 1.2));
        ctx.lineWidth = i ? R * 0.025 : 1;
        ctx.beginPath();
        ctx.moveTo(cx + Math.sin(a) * R * 0.3, cy - Math.cos(a) * R * 0.3);
        ctx.lineTo(cx + Math.sin(a) * R * 0.75, cy - Math.cos(a) * R * 0.75);
        ctx.stroke();
      }
    }
    if (S.state === "boot") ringArc(cx, cy, R * 0.988, 0, Math.max(0.02, S.boot || 0) * TAU, R * 0.006, 0.9);
    if (live.warn > 0.01) ringArc(cx, cy, R * 0.903, -t * 0.25, TAU, R * 0.006, live.warn * (0.45 + 0.3 * Math.sin(t * 4)), [R * 0.03, R * 0.03]);

    // centre
    const coreR = R * 0.18 * (1 + 0.18 * A);
    const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, coreR);
    g.addColorStop(0, rgba((0.5 + 0.5 * A) * bright));
    g.addColorStop(0.2, rgba(0.4 * (0.5 + 0.5 * A) * bright));
    g.addColorStop(1, rgba(0));
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.arc(cx, cy, coreR, 0, TAU); ctx.fill();
    ringArc(cx, cy, R * 0.152, 0, TAU, Math.max(1, R * 0.0035), 0.85 * bright);
    ringArc(cx, cy, R * 0.191, 0.3 + t * 0.35 * live.spin * motion, 5.0, Math.max(1, R * 0.002), 0.55 * bright);
    ctx.globalCompositeOperation = "source-over";
  }
  requestAnimationFrame(draw);
};
