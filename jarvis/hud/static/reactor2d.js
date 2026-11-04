/* J.A.R.V.I.S. 2D arc reactor: used only if 3D (WebGL2) isn't available on this Mac.
   Reads the same window.HUD_SCENE state as scene.js. */
"use strict";

window.startReactor2D = function startReactor2D(canvas) {
  const S = window.HUD_SCENE;
  const ctx = canvas.getContext("2d");
  const COLORS = {
    boot: [69, 227, 255], standby: [40, 160, 190], listening: [69, 227, 255], transcribing: [140, 150, 255],
    thinking: [255, 181, 71], speaking: [120, 236, 255], confirm: [255, 91, 58], muted: [110, 135, 150],
  };
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const color = [...COLORS.boot];
  const rot = { a: 0, b: 0, c: 0, sweep: 0 };
  let smooth = 0, last = performance.now();

  function resize() {
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.round(canvas.clientWidth * dpr));
    canvas.height = Math.max(1, Math.round(canvas.clientHeight * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  window.addEventListener("resize", resize);
  resize();

  const rgba = (c, a) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`;
  function arc(x, y, r, a0, a1, width, style) {
    ctx.beginPath(); ctx.arc(x, y, r, a0, a1); ctx.lineWidth = width; ctx.strokeStyle = style; ctx.stroke();
  }
  canvas.addEventListener("click", (ev) => {
    const v = S.view || { x: canvas.clientWidth / 2, y: canvas.clientHeight * 0.46, w: canvas.clientWidth, h: canvas.clientHeight };
    if (Math.hypot(ev.clientX - v.x, ev.clientY - v.y) < Math.min(v.w, v.h) * 0.15) {
      window.dispatchEvent(new CustomEvent("jarvis-core-click"));
    }
  });

  function draw(now) {
    requestAnimationFrame(draw);
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    const t = now / 1000;
    const w = canvas.clientWidth, h = canvas.clientHeight;
    const v = S.view;                          // the free space between the panels (from hud.js)
    const cx = v ? v.x : w / 2, cy = v ? v.y : h * 0.46;
    const R = v ? Math.min(v.w, v.h) * 0.4 : Math.min(w, h) * 0.27;
    ctx.fillStyle = "#01060b";
    ctx.fillRect(0, 0, w, h);
    const target = COLORS[S.state] || COLORS.standby;
    for (let i = 0; i < 3; i++) color[i] += (target[i] - color[i]) * Math.min(1, dt * 4);
    const raw = S.state === "speaking" ? S.levels.out : (S.state === "listening" || S.state === "confirm") ? S.levels.mic : 0;
    const level = Math.min(1, Math.sqrt(Math.max(0, raw || 0)) * 2.6);
    smooth += (level - smooth) * Math.min(1, dt * (level > smooth ? 18 : 5));
    const L = smooth, c = color;
    const busy = S.state === "thinking" ? 4 : S.state === "transcribing" ? 2.5 : 1;
    const motion = reduceMotion ? 0.15 : 1;
    rot.a += dt * 0.05 * busy * motion; rot.b -= dt * 0.18 * busy * motion; rot.c += dt * 0.4 * busy * motion;
    rot.sweep += dt * (S.state === "thinking" ? 3.2 : 0.9) * motion;
    const breathe = 0.5 + 0.5 * Math.sin(t * 1.6);

    const glow = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 2.2);
    glow.addColorStop(0, rgba(c, 0.18)); glow.addColorStop(1, rgba(c, 0));
    ctx.fillStyle = glow; ctx.fillRect(0, 0, w, h);
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    ctx.save(); ctx.translate(cx, cy); ctx.rotate(rot.a);
    for (let i = 0; i < 120; i++) {
      const a = (i / 120) * Math.PI * 2, long = i % 10 === 0;
      const r0 = R * (long ? 0.93 : 0.955), r1 = R * 0.985;
      ctx.beginPath(); ctx.moveTo(Math.cos(a) * r0, Math.sin(a) * r0); ctx.lineTo(Math.cos(a) * r1, Math.sin(a) * r1);
      ctx.lineWidth = long ? 2 : 1; ctx.strokeStyle = rgba(c, long ? 0.75 : 0.32); ctx.stroke();
    }
    ctx.restore();
    if (S.state === "boot") arc(cx, cy, R * 0.88, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * Math.max(0.03, S.boot || 0), 3, rgba(c, 0.9));
    if (S.state === "confirm") arc(cx, cy, R * 0.88, 0, Math.PI * 2, 2 + 3 * breathe, rgba(c, 0.25 + 0.5 * breathe));
    const segs = [0.0, 0.22, 0.31, 0.47, 0.6, 0.74, 0.83, 0.95];
    for (let i = 0; i < segs.length; i += 2) arc(cx, cy, R * 0.82, rot.b + segs[i] * Math.PI * 2, rot.b + segs[i + 1] * Math.PI * 2, 4, rgba(c, 0.7));
    ctx.save(); ctx.setLineDash([2, 7]); ctx.lineDashOffset = -rot.c * 40; arc(cx, cy, R * 0.735, 0, Math.PI * 2, 2, rgba(c, 0.45)); ctx.restore();
    for (let i = 0; i < 96; i++) {
      const a = (i / 96) * Math.PI * 2 - Math.PI / 2;
      const n = 0.5 + 0.5 * Math.sin(i * 1.7 + t * 9) * Math.sin(i * 0.43 - t * 5.3);
      const len = R * (0.05 + L * (0.08 + 0.2 * n)), r0 = R * 0.535;
      ctx.beginPath(); ctx.moveTo(cx + Math.cos(a) * r0, cy + Math.sin(a) * r0);
      ctx.lineTo(cx + Math.cos(a) * (r0 + len), cy + Math.sin(a) * (r0 + len));
      ctx.lineWidth = 2; ctx.strokeStyle = rgba(c, 0.35 + 0.6 * Math.min(1, L * 1.4)); ctx.stroke();
    }
    for (let i = 0; i < 10; i++) {
      const a0 = rot.a * -2 + (i / 10) * Math.PI * 2 + 0.07, a1 = a0 + Math.PI / 5 - 0.14;
      arc(cx, cy, R * 0.43, a0, a1, R * 0.07, rgba(c, 0.22 + 0.25 * L + 0.08 * breathe));
    }
    const coreR = R * (0.27 + 0.05 * L + 0.012 * breathe);
    const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, coreR * 1.9);
    g.addColorStop(0, `rgba(255,255,255,${0.85 + 0.15 * L})`); g.addColorStop(0.18, rgba(c, 0.75 + 0.2 * L));
    g.addColorStop(0.5, rgba(c, 0.18 + 0.25 * L)); g.addColorStop(1, rgba(c, 0));
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(cx, cy, coreR * 1.9, 0, Math.PI * 2); ctx.fill();
    arc(cx, cy, coreR, 0, Math.PI * 2, 2, rgba(c, 0.9));
    ctx.restore();
  }
  requestAnimationFrame(draw);
};
