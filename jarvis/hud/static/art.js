/* J.A.R.V.I.S. instrument artwork, shared by the 3D display (scene.js) and its 2D backup (reactor2d.js).

   The core is drawn like a precision instrument: hairline scales, arcs and small live readouts on
   transparent layers. Each layer is painted once, in white, on a square canvas; the displays tint it
   with the current state's colour and turn each layer at its own slow speed. Sizes below are in
   "instrument units": 1.0 is the radius of the outer scale. */
"use strict";

(function () {
  const TAU = Math.PI * 2;
  const W = 1.35;                 // all hairlines are drawn this much thicker than the numbers below
  const MONO = '"SF Mono", ui-monospace, Menlo, Monaco, "DejaVu Sans Mono", monospace';

  // Colour (sRGB, 0 to 1) and behaviour for each state: brightness, how fast the rings turn,
  // how strong the radar sweep is, and whether the "working" arc runs.
  const LOOKS = {
    boot:         { color: [0.56, 0.84, 0.93], level: 0.85, spin: 1.4, sweep: 0.6, activity: 1 },
    standby:      { color: [0.56, 0.84, 0.93], level: 0.92, spin: 0.55, sweep: 0.2, activity: 0 },
    listening:    { color: [0.74, 0.95, 1.00], level: 1.00, spin: 0.8, sweep: 0.0, activity: 0 },
    transcribing: { color: [0.67, 0.73, 1.00], level: 0.95, spin: 1.5, sweep: 0.0, activity: 1 },
    thinking:     { color: [1.00, 0.78, 0.50], level: 1.00, spin: 2.2, sweep: 1.0, activity: 1 },
    speaking:     { color: [0.80, 0.96, 1.00], level: 1.00, spin: 0.8, sweep: 0.0, activity: 0 },
    confirm:      { color: [1.00, 0.50, 0.38], level: 1.00, spin: 0.3, sweep: 0.0, activity: 0 },
    muted:        { color: [0.48, 0.56, 0.60], level: 0.62, spin: 0.2, sweep: 0.0, activity: 0 },
  };

  // ---------------------------------------------------------------- drawing helpers
  // Angles are measured clockwise from 12 o'clock, like a clock face.
  const polar = (r, a) => [Math.sin(a) * r, -Math.cos(a) * r];

  function ring(g, r, width, alpha = 1, dash = null) {
    g.save();
    g.globalAlpha = alpha;
    g.lineWidth = width * W;
    if (dash) g.setLineDash(dash);
    g.beginPath();
    g.arc(0, 0, r, 0, TAU);
    g.stroke();
    g.restore();
  }
  function arc(g, r, from, to, width, alpha = 1) {
    g.save();
    g.globalAlpha = alpha;
    g.lineWidth = width * W;
    g.beginPath();
    g.arc(0, 0, r, from - Math.PI / 2, to - Math.PI / 2);
    g.stroke();
    g.restore();
  }
  // Radial tick marks pointing inwards from radius r; `keep(i)` picks which of the `count` to draw.
  function ticks(g, r, count, length, width, alpha = 1, keep = null) {
    g.save();
    g.globalAlpha = alpha;
    g.lineWidth = width * W;
    g.beginPath();
    for (let i = 0; i < count; i++) {
      if (keep && !keep(i)) continue;
      const a = (i / count) * TAU;
      const [x0, y0] = polar(r, a), [x1, y1] = polar(r - length, a);
      g.moveTo(x0, y0);
      g.lineTo(x1, y1);
    }
    g.stroke();
    g.restore();
  }
  function text(g, str, x, y, size, alpha = 0.8, weight = 500, align = "center") {
    g.save();
    g.globalAlpha = alpha;
    g.textAlign = align;
    g.font = `${weight} ${size}px ${MONO}`;
    g.fillText(str, x, y);
    g.restore();
  }
  // Text that follows a circle, centred on angle `centre`. On the lower half (`bottom`) it reads
  // left to right with the tops of the letters towards the middle.
  function arcText(g, str, r, centre, size, { alpha = 0.85, weight = 500, spacing = 0.22, bottom = false } = {}) {
    g.save();
    g.globalAlpha = alpha;
    g.font = `${weight} ${size}px ${MONO}`;
    const chars = [...str];
    const widths = chars.map((c) => g.measureText(c).width + size * spacing);
    const total = widths.reduce((a, b) => a + b, 0) / r;
    let a = bottom ? centre + total / 2 : centre - total / 2;
    chars.forEach((c, i) => {
      const step = widths[i] / r;
      g.save();
      if (bottom) { g.rotate(a - step / 2 - Math.PI); g.translate(0, r); } else { g.rotate(a + step / 2); g.translate(0, -r); }
      g.fillText(c, 0, 0);
      g.restore();
      a += bottom ? -step : step;
    });
    g.restore();
  }

  // ---------------------------------------------------------------- the layers (u = pixels per unit)
  function backdrop(g, u) {          // faint, large rings and bearing lines far behind the instrument
    const haze = g.createRadialGradient(0, 0, 0, 0, 0, 1.25 * u);
    haze.addColorStop(0, "rgba(255,255,255,0.16)");
    haze.addColorStop(1, "rgba(255,255,255,0)");
    g.save();
    g.shadowBlur = 0;
    g.fillStyle = haze;
    g.fillRect(-1.3 * u, -1.3 * u, 2.6 * u, 2.6 * u);
    g.restore();
    [1.15, 1.45, 1.8, 2.2].forEach((r, i) => ring(g, r * u, 0.003 * u, 0.5 - i * 0.08, i % 2 ? [0.01 * u, 0.028 * u] : null));
    ticks(g, 2.2 * u, 72, 0.025 * u, 0.003 * u, 0.4);
    ticks(g, 1.15 * u, 24, -1.05 * u, 0.0022 * u, 0.2);            // bearing lines, outwards
  }

  function scale(g, u) {             // the outer degree scale; it doesn't turn, like a bezel
    const r = 0.965 * u;
    ring(g, r, 0.0026 * u);
    ring(g, r - 0.012 * u, 0.0011 * u, 0.45);
    ticks(g, r, 360, 0.012 * u, 0.0013 * u, 0.5, (i) => i % 5 !== 0);
    ticks(g, r, 360, 0.022 * u, 0.0018 * u, 0.75, (i) => i % 5 === 0 && i % 10 !== 0);
    ticks(g, r, 360, 0.036 * u, 0.0026 * u, 1, (i) => i % 10 === 0);
    for (let d = 0; d < 360; d += 30) {
      g.save();
      g.rotate((d / 360) * TAU);
      text(g, String(d).padStart(3, "0"), 0, -(r - 0.064 * u), 0.03 * u, 0.75);
      g.restore();
    }
    for (let q = 0; q < 4; q++) {      // small triangles at the quarters
      g.save();
      g.rotate((q / 4) * TAU);
      g.globalAlpha = 0.9;
      g.beginPath();
      g.moveTo(0, -r - 0.012 * u);
      g.lineTo(-0.011 * u, -r - 0.034 * u);
      g.lineTo(0.011 * u, -r - 0.034 * u);
      g.closePath();
      g.fill();
      g.restore();
    }
  }

  function arcs(g, u) {              // broken arcs, a dashed ring and lettering along the rings
    const r = 0.8 * u;
    [[0.02, 0.21], [0.25, 0.33], [0.4, 0.61], [0.66, 0.71], [0.76, 0.97]].forEach(([a, b]) => {
      arc(g, r, a * TAU, b * TAU, 0.004 * u, 0.85);
      g.save();
      g.globalAlpha = 0.85;
      g.lineWidth = 0.002 * u * W;
      g.beginPath();
      [a, b].forEach((t) => {
        const [x0, y0] = polar(r - 0.012 * u, t * TAU), [x1, y1] = polar(r + 0.012 * u, t * TAU);
        g.moveTo(x0, y0);
        g.lineTo(x1, y1);
      });
      g.stroke();
      g.restore();
    });
    ring(g, r - 0.03 * u, 0.0014 * u, 0.55, [0.006 * u, 0.014 * u]);
    arcText(g, "JUST A RATHER VERY INTELLIGENT SYSTEM", r + 0.036 * u, 0, 0.025 * u);
    arcText(g, "ON-DEVICE SPEECH  ·  CLAUDE CODE  ·  LOCAL LINK", r + 0.036 * u, Math.PI, 0.025 * u,
      { bottom: true, alpha: 0.6 });
    for (let q = 0; q < 4; q++) arc(g, 0.87 * u, (q / 4 + 0.115) * TAU, (q / 4 + 0.135) * TAU, 0.01 * u, 0.95);
  }

  function inner(g, u) {             // inner rings, fine graduations and four crosshair scales
    ring(g, 0.52 * u, 0.002 * u, 0.85);
    ring(g, 0.5 * u, 0.0011 * u, 0.5, [0.003 * u, 0.01 * u]);
    ring(g, 0.455 * u, 0.0015 * u, 0.45);
    ticks(g, 0.455 * u, 120, 0.012 * u, 0.0013 * u, 0.45);
    ticks(g, 0.6 * u, 240, 0.008 * u, 0.0011 * u, 0.3);
    g.save();
    g.globalAlpha = 0.65;
    g.lineWidth = 0.0015 * u * W;
    g.beginPath();
    for (let q = 0; q < 4; q++) {
      const a = (q / 4) * TAU;
      const along = (d, side) => {
        const [x, y] = polar(d * u, a);
        return [x + Math.cos(a) * side * u, y + Math.sin(a) * side * u];
      };
      let p = along(0.54, 0); g.moveTo(...p);
      p = along(0.7, 0); g.lineTo(...p);
      for (let k = 0; k <= 8; k++) {
        const d = 0.54 + k * 0.02, w = k % 4 === 0 ? 0.014 : 0.007;
        g.moveTo(...along(d, -w));
        g.lineTo(...along(d, w));
      }
    }
    g.stroke();
    g.restore();
  }

  // Live readouts in four small frames; `d` holds the values hud.js last received.
  function readouts(g, u, d) {
    const items = [[315, "CPU", d.cpu], [45, "MEMORY", d.mem], [135, "NETWORK", d.net], [225, "AI CORE", d.core]];
    const w = 0.2 * u, h = 0.086 * u;
    items.forEach(([deg, name, value]) => {
      const [x, y] = polar(0.665 * u, (deg / 360) * TAU);
      g.save();
      g.translate(x, y);
      g.globalAlpha = 0.5;
      g.lineWidth = 0.0016 * u * W;
      g.strokeRect(-w / 2, -h / 2, w, h);
      g.globalAlpha = 0.9;
      g.fillRect(-w / 2, -h / 2, 0.005 * u, h);
      text(g, name, -w / 2 + 0.018 * u, -h * 0.22, 0.021 * u, 0.65, 500, "left");
      text(g, value || "--", -w / 2 + 0.018 * u, h * 0.22, 0.032 * u, 1, 600, "left");
      g.restore();
    });
  }

  // radius: how far the layer reaches (units); z: depth; spin: how fast it turns (0 = fixed);
  // px: canvas size at full quality; live: repainted when the readouts change.
  const LAYERS = [
    { name: "backdrop", draw: backdrop, radius: 2.3, z: -0.55, opacity: 0.22, spin: 0.08, px: 2048 },
    { name: "scale", draw: scale, radius: 1.0, z: 0.0, opacity: 1.0, spin: 0, px: 2048 },
    { name: "readouts", draw: readouts, radius: 0.8, z: 0.03, opacity: 1.0, spin: 0, px: 1600, live: true },
    { name: "arcs", draw: arcs, radius: 1.0, z: 0.06, opacity: 0.95, spin: -1.0, px: 2048 },
    { name: "inner", draw: inner, radius: 1.0, z: 0.12, opacity: 0.9, spin: 0.6, px: 2048 },
  ];

  // Paints one layer (into `canvas` if given, so a live layer can be redrawn in place).
  function paint(layer, data, canvas, factor = 1) {
    const px = Math.max(64, Math.round(layer.px * factor));
    const cv = canvas || document.createElement("canvas");
    cv.width = cv.height = px;                      // also clears it
    const g = cv.getContext("2d");
    const u = px / 2 / layer.radius;
    g.translate(px / 2, px / 2);
    g.strokeStyle = g.fillStyle = "#ffffff";
    g.lineCap = "butt";
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.shadowColor = "rgba(255,255,255,0.6)";       // a little light bleeding from every line
    g.shadowBlur = 0.005 * u;
    layer.draw(g, u, data || {});
    return cv;
  }

  // The small, bright centre: a soft point of light.
  function core(size = 256) {
    const cv = document.createElement("canvas");
    cv.width = cv.height = size;
    const g = cv.getContext("2d");
    const c = size / 2;
    const grad = g.createRadialGradient(c, c, 0, c, c, c);
    grad.addColorStop(0, "rgba(255,255,255,1)");
    grad.addColorStop(0.1, "rgba(255,255,255,0.85)");
    grad.addColorStop(0.28, "rgba(255,255,255,0.2)");
    grad.addColorStop(0.6, "rgba(255,255,255,0.04)");
    grad.addColorStop(1, "rgba(255,255,255,0)");
    g.fillStyle = grad;
    g.fillRect(0, 0, size, size);
    return cv;
  }

  window.JARVIS_ART = { TAU, MONO, LOOKS, LAYERS, paint, core, polar };
})();
