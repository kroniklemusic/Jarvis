/* J.A.R.V.I.S. heads-up display.
   Receives live events from JARVIS (server-sent events) and sends button presses back.
   Open with ?demo=1 to see it without JARVIS running (optionally &scene=listening|thinking|speaking|confirm|standby|boot). */
"use strict";

const params = new URLSearchParams(location.search);
const TOKEN = params.get("token") || "";
const DEMO = params.has("demo") || !TOKEN;
const $ = (id) => document.getElementById(id);

const STATES = {
  boot:      { name: "Initialising",  sub: "Bringing systems online" },
  standby:   { name: "Standing By",   sub: "" },
  listening: { name: "Listening",     sub: "Speak now" },
  thinking:  { name: "Processing",    sub: "Consulting Claude" },
  speaking:  { name: "Responding",    sub: "" },
  confirm:   { name: "Authorisation", sub: "Awaiting your decision" },
};
const COLORS = {
  boot: [69, 227, 255], standby: [40, 160, 190], listening: [69, 227, 255],
  thinking: [255, 181, 71], speaking: [120, 236, 255], confirm: [255, 91, 58],
};

const hud = {
  state: "boot", followUp: false, ready: false, hotkey: "Control + Option + J",
  levels: { mic: 0, out: 0 }, smooth: 0, color: [...COLORS.boot], boot: 0,
  started: performance.now(), uptimeBase: null, entries: 0, jarvisEntry: null,
};

/* ------------------------------------------------------------------ text helpers */
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;          // never innerHTML: replies may contain web text
  return e;
}
const pad = (n) => String(n).padStart(2, "0");
const hhmm = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
const fmtDuration = (s) => `${pad(Math.floor(s / 3600))}:${pad(Math.floor(s / 60) % 60)}:${pad(Math.floor(s % 60))}`;

/* ------------------------------------------------------------------ comms log */
const log = $("log");
function scrollLog() { log.scrollTop = log.scrollHeight; }
function stamp(entry) { entry.appendChild(el("span", "ts", hhmm(new Date()))); }
function countEntry() { $("comms-count").textContent = String(++hud.entries).padStart(3, "0"); }

function addSpeech(who, text, instant) {
  const entry = el("div", `entry ${who}`);
  entry.appendChild(el("span", "who", who === "you" ? "You" : "J.A.R.V.I.S."));
  stamp(entry);
  const p = el("p");
  entry.appendChild(p);
  log.appendChild(entry);
  countEntry();
  typeInto(p, text, instant);
  scrollLog();
  return p;
}
function addLine(cls, text) {
  log.appendChild(el("div", `entry ${cls}`, text));
  while (log.children.length > 250) log.firstChild.remove();
  scrollLog();
}

/* typewriter: fast enough to keep pace with speech, never blocks */
const typing = [];
function typeInto(p, text, instant) {
  const job = typing.find((j) => j.p === p);
  if (job) { job.text += " " + text; return; }            // keep one typing job per line, in order
  const lead = p.textContent ? " " : "";
  if (instant) { p.textContent += lead + text; return; }
  typing.push({ p, text: lead + text, i: 0 });
  p.classList.add("caret");
}
function tickTyping() {
  for (let k = typing.length - 1; k >= 0; k--) {
    const job = typing[k];
    const step = Math.max(1, Math.ceil(job.text.length / 45));
    job.p.textContent += job.text.slice(job.i, job.i + step);
    job.i += step;
    if (job.i >= job.text.length) { job.p.classList.remove("caret"); typing.splice(k, 1); }
  }
  if (typing.length) scrollLog();
}

let captionTimer = null;
function caption(text) {
  const c = $("caption");
  c.classList.remove("fade");
  c.textContent = text;
  clearTimeout(captionTimer);
  captionTimer = setTimeout(() => c.classList.add("fade"), 6000);
}

/* ------------------------------------------------------------------ state */
function setState(state, followUp) {
  hud.state = state;
  hud.followUp = !!followUp;
  document.body.className = `state-${state}`;
  const info = STATES[state] || STATES.standby;
  $("state-name").textContent = info.name;
  let sub = info.sub;
  if (state === "standby") sub = `Say "hey Jarvis" · ${hud.hotkey}`;
  if (state === "listening" && followUp) sub = "Go ahead — no wake word needed";
  $("state-sub").textContent = sub;
  $("hint").textContent = {
    standby: 'Say "hey Jarvis"', listening: "Listening…", thinking: "Esc to cancel",
    speaking: 'Say "hey Jarvis" or Esc to interrupt', confirm: "Y to authorise · N to deny",
  }[state] || "";
  if (state === "standby" || state === "listening") hud.jarvisEntry = null;
  setSubsystem("claude", state === "thinking" ? "busy" : hud.claudeOnline === false ? "down" : hud.ready ? "ok" : "");
}

function setSubsystem(key, status) {
  const li = document.querySelector(`#subsystems li[data-key="${key}"]`);
  if (li) li.className = status || "";
}

/* ------------------------------------------------------------------ boot */
function bootProgress(d) {
  const steps = document.querySelectorAll("#boot-steps li");
  steps.forEach((li) => {
    const n = Number(li.dataset.step);
    li.className = d.ready || n < d.step ? "done" : n === d.step ? "busy" : "";
  });
  hud.boot = d.ready ? 1 : (d.step - 1) / d.total;
  $("boot-bar").style.width = `${Math.round((d.ready ? 1 : (d.step - 0.5) / d.total) * 100)}%`;
  ["wake", "speech", "voice"].forEach((k, i) => setSubsystem(k, d.ready || d.step > i + 1 ? "ok" : d.step === i + 1 ? "busy" : ""));
  if (d.ready && !hud.ready) {
    hud.ready = true;
    setTimeout(() => $("boot").classList.add("hidden"), 700);
    if (hud.state === "boot") setState("standby");
  }
}
function bootLine(text) {
  const lines = $("boot-lines");
  lines.appendChild(el("div", null, `> ${text}`));
  while (lines.children.length > 8) lines.firstChild.remove();
}

/* ------------------------------------------------------------------ panels */
function applyConfig(d) {
  $("r-model").textContent = `Claude · ${d.model || "sonnet"}`;
  const where = d.voice_device === "cpu" ? " · CPU" : d.voice_device === "mps" ? " · GPU" : "";
  $("r-voice").textContent = `${d.voice || "—"}${d.voice_detail ? " · " + d.voice_detail : ""}${where}`;
  $("r-speech").textContent = (d.speech_model || "—").replace(/^mlx-community\//, "");
  $("r-memory").textContent = d.memory_gb ? `${Math.round(d.memory_gb)} GB` : "—";
  if (d.hotkey) hud.hotkey = d.hotkey;
  const tools = $("tools");
  tools.textContent = "";
  const names = { "macos-automator": "Mac control", playwright: "Browser", duckduckgo: "Web search", "google-workspace": "Google" };
  (d.tools && d.tools.length ? d.tools : []).forEach((t) => {
    const li = el("li", null, names[t] || t);
    li.dataset.tool = t;
    tools.appendChild(li);
  });
  if (!tools.children.length) tools.appendChild(el("li", "muted", "None"));
  if (hud.state === "standby") setState("standby");
}
function applyTelemetry(d) {
  if (d.load != null && d.cores) setMeter("load", Math.min(1, d.load / d.cores), `${Math.round((d.load / d.cores) * 100)}%`);
  if (d.uptime_s != null) hud.uptimeBase = { s: d.uptime_s, at: performance.now() };
  if (d.swap_gb != null && $("r-memory").textContent !== "—") {
    const base = $("r-memory").textContent.split(" · ")[0];
    $("r-memory").textContent = d.swap_gb >= 0.5 ? `${base} · ${d.swap_gb.toFixed(1)} GB SWAP` : base;
  }
  hud.claudeOnline = d.claude_online;
  setSubsystem("claude", d.claude_online === false ? "down" : hud.state === "thinking" ? "busy" : "ok");
}
function setMeter(name, frac, label) {
  const bar = $(`m-${name}`);
  bar.style.width = `${Math.round(Math.max(0, Math.min(1, frac)) * 100)}%`;
  bar.classList.toggle("hot", frac > 0.85);
  $(`m-${name}-v`).textContent = label;
}
function showLatency(d) {
  const box = $("latency");
  box.textContent = "Latency  ";
  const parts = [["Speech", d.transcribe_s], ["Claude", d.claude_s], ["Voice", d.voice_s]].filter(([, v]) => typeof v === "number");
  parts.forEach(([label, value], i) => {
    if (i) box.append("  ·  ");
    box.append(`${label} `);
    box.appendChild(el("b", null, `${value.toFixed(1)}s`));
  });
  if (d.behind_s >= 0.5) box.append(`  ·  ${d.behind_s.toFixed(1)}s behind`);
}
function flashTool(label, name) {
  const server = (name || "").split("__")[1];
  const li = server && document.querySelector(`#tools li[data-tool="${server}"]`);
  if (li) { li.classList.add("active"); setTimeout(() => li.classList.remove("active"), 2500); }
}

/* ------------------------------------------------------------------ overlays */
function showConfirm(d) {
  $("confirm-text").textContent = d.question || "";
  $("confirm-tool").textContent = d.tool ? `Requested: ${d.tool}` : "";
  $("confirm").classList.remove("hidden");
}
function hideConfirm() { $("confirm").classList.add("hidden"); }
function showFatal(text) { $("fatal-text").textContent = text; $("fatal").classList.remove("hidden"); }

/* ------------------------------------------------------------------ events */
function receive(ev) {
  const d = ev.data || {};
  const instant = ev.t && Date.now() / 1000 - ev.t > 3;     // replayed history: no animation
  switch (ev.kind) {
    case "state": setState(d.state, d.follow_up); break;
    case "level":
      if ("mic" in d) hud.levels.mic = d.mic;
      if ("out" in d) hud.levels.out = d.out;
      break;
    case "heard": addSpeech("you", d.text, true); hud.jarvisEntry = null; if (!instant) caption(`“${d.text}”`); break;
    case "said":
      if (hud.jarvisEntry && hud.jarvisEntry.isConnected) typeInto(hud.jarvisEntry, d.text, instant);
      else hud.jarvisEntry = addSpeech("jarvis", d.text, instant);
      if (!instant) caption(d.text);
      break;
    case "tool": addLine("tool", d.label || d.name); if (!instant) flashTool(d.label, d.name); break;
    case "notice": if (hud.ready) addLine("sys", d.text); else bootLine(d.text); break;
    case "boot": bootProgress(d); break;
    case "config": applyConfig(d); break;
    case "telemetry": applyTelemetry(d); break;
    case "permission": hud.jarvisEntry = null; if (!instant) showConfirm(d); addLine("perm", `Authorisation requested — ${d.tool || "action"}`); break;
    case "permission_done": hideConfirm(); addLine(d.allowed ? "perm ok" : "perm", d.allowed ? "Authorised" : "Denied"); break;
    case "timing": showLatency(d); break;
    case "fatal": showFatal(d.text); break;
  }
}

function setLink(status) {
  const link = $("link");
  link.className = `link ${status === "ONLINE" ? "online" : status === "LINK LOST" ? "lost" : ""}`;
  $("link-text").textContent = status;
}

function connect() {
  const source = new EventSource(`/events?token=${encodeURIComponent(TOKEN)}`);
  source.onopen = () => setLink("ONLINE");
  source.onmessage = (e) => { try { receive(JSON.parse(e.data)); } catch (err) { console.error(err); } };
  source.onerror = () => setLink("LINK LOST");
}

function command(name) {
  if (DEMO) return demoCommand(name);
  fetch(`/command?token=${encodeURIComponent(TOKEN)}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ command: name }),
  }).catch(() => setLink("LINK LOST"));
}

$("btn-talk").addEventListener("click", () => command("talk"));
$("btn-stop").addEventListener("click", () => command("stop"));
$("btn-yes").addEventListener("click", () => command("yes"));
$("btn-no").addEventListener("click", () => command("no"));
document.addEventListener("keydown", (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey || e.repeat) return;
  const confirming = !$("confirm").classList.contains("hidden");
  if (e.code === "Space") { e.preventDefault(); command("talk"); }
  else if (e.key === "Escape") { e.preventDefault(); command("stop"); }
  else if (confirming && e.key.toLowerCase() === "y") command("yes");
  else if (confirming && e.key.toLowerCase() === "n") command("no");
});

/* ------------------------------------------------------------------ clocks */
function tickClocks() {
  const now = new Date();
  const hms = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
  $("clock-small").textContent = hms;
  $("clock-big").textContent = hhmm(now);
  $("date").textContent = now.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  const up = hud.uptimeBase ? hud.uptimeBase.s + (performance.now() - hud.uptimeBase.at) / 1000 : (performance.now() - hud.started) / 1000;
  $("uptime").textContent = fmtDuration(up);
}

/* ------------------------------------------------------------------ the reactor */
const canvas = $("reactor");
const ctx = canvas.getContext("2d");
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
let rot = { a: 0, b: 0, c: 0, sweep: 0 };
let last = performance.now();

function resize() {
  const dpr = window.devicePixelRatio || 1;
  const r = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, Math.round(r.width * dpr));
  canvas.height = Math.max(1, Math.round(r.height * dpr));
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
window.addEventListener("resize", resize);

const rgba = (c, a) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`;
function arc(x, y, r, a0, a1, width, color) {
  ctx.beginPath(); ctx.arc(x, y, r, a0, a1); ctx.lineWidth = width; ctx.strokeStyle = color; ctx.stroke();
}

function draw(now) {
  const dt = Math.min(0.1, (now - last) / 1000);
  last = now;
  const t = now / 1000;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  const cx = w / 2, cy = h / 2 - h * 0.04, R = Math.min(w, h) * 0.44;
  ctx.clearRect(0, 0, w, h);

  // colour eases towards the state's colour; level eases towards the live audio level
  const target = COLORS[hud.state] || COLORS.standby;
  for (let i = 0; i < 3; i++) hud.color[i] += (target[i] - hud.color[i]) * Math.min(1, dt * 4);
  const c = hud.color;
  const raw = hud.state === "speaking" ? hud.levels.out : hud.state === "listening" || hud.state === "confirm" ? hud.levels.mic : 0;
  const level = Math.min(1, Math.sqrt(Math.max(0, raw)) * 2.6);
  hud.smooth += (level - hud.smooth) * Math.min(1, dt * (level > hud.smooth ? 18 : 5));
  const L = hud.smooth;
  const busy = hud.state === "thinking" ? 4 : hud.state === "boot" ? 2 : 1;
  const motion = reduceMotion ? 0.15 : 1;
  rot.a += dt * 0.05 * busy * motion;
  rot.b -= dt * 0.18 * busy * motion;
  rot.c += dt * 0.4 * busy * motion;
  rot.sweep += dt * (hud.state === "thinking" ? 3.2 : 0.9) * motion;
  const breathe = 0.5 + 0.5 * Math.sin(t * (hud.state === "standby" ? 1.1 : 2.2));

  ctx.save();
  ctx.globalCompositeOperation = "lighter";

  // 1. outer tick ring
  ctx.save(); ctx.translate(cx, cy); ctx.rotate(rot.a);
  for (let i = 0; i < 120; i++) {
    const a = (i / 120) * Math.PI * 2, long = i % 10 === 0;
    const r0 = R * (long ? 0.93 : 0.955), r1 = R * 0.985;
    ctx.beginPath(); ctx.moveTo(Math.cos(a) * r0, Math.sin(a) * r0); ctx.lineTo(Math.cos(a) * r1, Math.sin(a) * r1);
    ctx.lineWidth = long ? 2 : 1; ctx.strokeStyle = rgba(c, long ? 0.75 : 0.32); ctx.stroke();
  }
  ctx.restore();
  arc(cx, cy, R * 0.92, 0, Math.PI * 2, 1, rgba(c, 0.25));

  // 2. boot progress / confirm warning ring
  if (hud.state === "boot") arc(cx, cy, R * 0.88, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * Math.max(0.03, hud.boot), 3, rgba(c, 0.9));
  if (hud.state === "confirm") arc(cx, cy, R * 0.88, 0, Math.PI * 2, 2 + 3 * breathe, rgba(c, 0.25 + 0.5 * breathe));

  // 3. segmented ring
  const segs = [0.0, 0.22, 0.31, 0.47, 0.6, 0.74, 0.83, 0.95];
  for (let i = 0; i < segs.length; i += 2) {
    const a0 = rot.b + segs[i] * Math.PI * 2, a1 = rot.b + segs[i + 1] * Math.PI * 2;
    arc(cx, cy, R * 0.82, a0, a1, 4, rgba(c, 0.7));
    arc(cx, cy, R * 0.795, a0 + 0.02, a1 - 0.02, 1, rgba(c, 0.35));
  }

  // 4. dashed ring
  ctx.save(); ctx.setLineDash([2, 7]); ctx.lineDashOffset = -rot.c * 40;
  arc(cx, cy, R * 0.735, 0, Math.PI * 2, 2, rgba(c, 0.45)); ctx.restore();

  // 5. sweep (thinking) or slow scanner
  const sweepAlpha = hud.state === "thinking" ? 0.55 : 0.16;
  const grad = ctx.createConicGradient ? ctx.createConicGradient(rot.sweep, cx, cy) : null;
  if (grad) {
    grad.addColorStop(0, rgba(c, sweepAlpha)); grad.addColorStop(0.12, rgba(c, 0)); grad.addColorStop(1, rgba(c, 0));
    ctx.beginPath(); ctx.arc(cx, cy, R * 0.7, 0, Math.PI * 2); ctx.arc(cx, cy, R * 0.52, 0, Math.PI * 2, true);
    ctx.fillStyle = grad; ctx.fill("evenodd");
  }

  // 6. audio bars
  const bars = 96;
  for (let i = 0; i < bars; i++) {
    const a = (i / bars) * Math.PI * 2 - Math.PI / 2;
    const n = 0.5 + 0.5 * Math.sin(i * 1.7 + t * 9) * Math.sin(i * 0.43 - t * 5.3);
    const idle = hud.state === "standby" ? 0.04 + 0.03 * breathe : 0.05;
    const len = R * (idle + L * (0.08 + 0.2 * n));
    const r0 = R * 0.535;
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(a) * r0, cy + Math.sin(a) * r0);
    ctx.lineTo(cx + Math.cos(a) * (r0 + len), cy + Math.sin(a) * (r0 + len));
    ctx.lineWidth = 2; ctx.strokeStyle = rgba(c, 0.35 + 0.6 * Math.min(1, L * 1.4)); ctx.stroke();
  }

  // 7. arc-reactor coils
  const coils = 10;
  for (let i = 0; i < coils; i++) {
    const a0 = rot.a * -2 + (i / coils) * Math.PI * 2 + 0.07, a1 = a0 + (Math.PI * 2) / coils - 0.14;
    arc(cx, cy, R * 0.43, a0, a1, R * 0.07, rgba(c, 0.22 + 0.25 * L + 0.08 * breathe));
    arc(cx, cy, R * 0.47, a0, a1, 1.2, rgba(c, 0.7));
    arc(cx, cy, R * 0.39, a0, a1, 1.2, rgba(c, 0.5));
  }

  // 8. core glow
  const coreR = R * (0.27 + 0.05 * L + 0.012 * breathe);
  const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, coreR * 1.9);
  g.addColorStop(0, `rgba(255,255,255,${0.85 + 0.15 * L})`);
  g.addColorStop(0.18, rgba(c, 0.75 + 0.2 * L));
  g.addColorStop(0.5, rgba(c, 0.18 + 0.25 * L));
  g.addColorStop(1, rgba(c, 0));
  ctx.fillStyle = g; ctx.beginPath(); ctx.arc(cx, cy, coreR * 1.9, 0, Math.PI * 2); ctx.fill();
  arc(cx, cy, coreR, 0, Math.PI * 2, 2, rgba(c, 0.9));
  // inner triangle (Mark VI reactor nod)
  ctx.save(); ctx.translate(cx, cy); ctx.rotate(-rot.c * 0.3);
  ctx.beginPath();
  for (let k = 0; k < 3; k++) {
    const a = -Math.PI / 2 + (k * Math.PI * 2) / 3;
    const x = Math.cos(a) * coreR * 0.62, y = Math.sin(a) * coreR * 0.62;
    k ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  }
  ctx.closePath(); ctx.lineWidth = 2; ctx.strokeStyle = `rgba(255,255,255,${0.35 + 0.4 * L})`; ctx.stroke();
  ctx.restore();

  // 9. orbiting markers
  for (let k = 0; k < 3; k++) {
    const a = rot.c * (k % 2 ? -1 : 1) + (k * Math.PI * 2) / 3, r = R * (0.62 + 0.02 * k);
    ctx.beginPath(); ctx.arc(cx + Math.cos(a) * r, cy + Math.sin(a) * r, 2.6, 0, Math.PI * 2);
    ctx.fillStyle = rgba(c, 0.95); ctx.fill();
  }
  ctx.restore();

  tickTyping();
  if (now - (hud.meterAt || 0) > 100) {                      // audio meters, 10 times a second
    hud.meterAt = now;
    const pct = (v) => Math.min(1, Math.sqrt(Math.max(0, v)) * 2.6);
    const m = pct(hud.levels.mic), o = pct(hud.levels.out);
    setMeter("mic", m, `${Math.round(m * 100)}%`);
    setMeter("out", o, `${Math.round(o * 100)}%`);
  }
  requestAnimationFrame(draw);
}

/* ------------------------------------------------------------------ demo mode */
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let demoLevel = null;
function demoCommand(name) {
  if (name === "yes" || name === "no") { hideConfirm(); receive({ kind: "permission_done", data: { allowed: name === "yes" } }); }
  if (name === "stop") { receive({ kind: "state", data: { state: "standby" } }); receive({ kind: "notice", data: { text: 'Stopped. Say "hey Jarvis" when you need me.' } }); }
  if (name === "talk") receive({ kind: "state", data: { state: "listening" } });
}
function demoLevels() {
  const t = performance.now() / 1000;
  const v = 0.012 + 0.02 * Math.abs(Math.sin(t * 3.1) * Math.sin(t * 7.3));
  hud.levels.mic = hud.state === "listening" ? v * (demoLevel ?? 1) : 0.001;
  hud.levels.out = hud.state === "speaking" ? v * 1.4 : 0;
}
const DEMO_CONFIG = { model: "sonnet", voice: "kokoro", voice_detail: "bm_george", voice_device: "cpu",
  speech_model: "mlx-community/whisper-small-mlx", hotkey: "Control + Option + J",
  tools: ["macos-automator", "playwright", "duckduckgo"], memory_gb: 16 };
function demoHistory() {
  const old = Date.now() / 1000 - 600;
  [["notice", { text: "Starting JARVIS on an Apple Silicon Mac." }],
   ["heard", { text: "Good morning. What's on my calendar today?" }],
   ["tool", { name: "mcp__macos-automator__execute_script", label: "Mac automation: execute script" }],
   ["said", { text: "Good morning. You have two engagements today: a design review at eleven, and dinner with Anna at seven." }],
   ["timing", { transcribe_s: 0.6, claude_s: 1.9, voice_s: 0.7 }],
  ].forEach(([kind, data]) => receive({ kind, data, t: old }));
}
async function runDemo() {
  setLink("DEMO");
  const scene = params.get("scene");
  const boot = async (fast) => {
    for (let s = 1; s <= 4; s++) {
      receive({ kind: "boot", data: { step: s, total: 4, label: ["Wake word", "Speech detector", "Speech recognition", "Voice"][s - 1] } });
      receive({ kind: "notice", data: { text: `Loading ${["wake word", "speech detector", "speech recognition", "voice"][s - 1]}…` } });
      await sleep(fast ? 0 : 700);
    }
    receive({ kind: "config", data: DEMO_CONFIG });
    receive({ kind: "telemetry", data: { uptime_s: 3725, load: 2.4, cores: 8, swap_gb: 0, claude_online: true } });
    receive({ kind: "boot", data: { step: 4, total: 4, label: "Ready", ready: true } });
  };
  setInterval(demoLevels, 50);
  if (scene === "boot") {
    receive({ kind: "boot", data: { step: 3, total: 4, label: "Speech recognition" } });
    ["Starting JARVIS on an Apple Silicon Mac.", "Loading models (about 20 seconds)...", "  1/4  Wake word", "  2/4  Speech detector", "  3/4  Speech recognition"].forEach((t) => bootLine(t));
    return;
  }
  if (scene) {
    await boot(true);
    demoHistory();
    const s = scene;
    if (s === "listening") { receive({ kind: "state", data: { state: "listening" } }); }
    if (s === "thinking") {
      receive({ kind: "heard", data: { text: "Find me a good Italian restaurant near Covent Garden." } });
      receive({ kind: "tool", data: { name: "mcp__duckduckgo__search", label: "DuckDuckGo: search" } });
      receive({ kind: "state", data: { state: "thinking" } });
    }
    if (s === "speaking") {
      receive({ kind: "heard", data: { text: "What's the weather like in London tomorrow?" } });
      receive({ kind: "tool", data: { name: "mcp__duckduckgo__search", label: "DuckDuckGo: search" } });
      receive({ kind: "said", data: { text: "Tomorrow looks mild in London, around fourteen degrees." } });
      receive({ kind: "said", data: { text: "Expect light rain from mid-afternoon, so an umbrella would be wise." } });
      receive({ kind: "state", data: { state: "speaking" } });
    }
    if (s === "confirm") {
      receive({ kind: "heard", data: { text: "Email Anna and tell her I'll be ten minutes late." } });
      receive({ kind: "state", data: { state: "confirm" } });
      receive({ kind: "permission", data: { question: "I'd like to run an AppleScript that controls apps on your Mac. Shall I proceed? Say yes, proceed, or no.", tool: "Mac automation: execute script" } });
    }
    if (s === "standby") receive({ kind: "state", data: { state: "standby" } });
    return;
  }
  await boot(false);
  demoHistory();
  for (;;) {
    receive({ kind: "state", data: { state: "standby" } }); await sleep(2500);
    receive({ kind: "state", data: { state: "listening" } }); await sleep(2600);
    receive({ kind: "heard", data: { text: "What's the weather like in London tomorrow?" } });
    receive({ kind: "state", data: { state: "thinking" } }); await sleep(900);
    receive({ kind: "tool", data: { name: "mcp__duckduckgo__search", label: "DuckDuckGo: search" } }); await sleep(1300);
    receive({ kind: "state", data: { state: "speaking" } });
    receive({ kind: "said", data: { text: "Tomorrow looks mild in London, around fourteen degrees." } }); await sleep(2600);
    receive({ kind: "said", data: { text: "Expect light rain from mid-afternoon, so an umbrella would be wise." } }); await sleep(3200);
    receive({ kind: "timing", data: { transcribe_s: 0.5, claude_s: 1.8, voice_s: 0.6 } });
    receive({ kind: "state", data: { state: "listening", follow_up: true } }); await sleep(2400);
    receive({ kind: "heard", data: { text: "Email Anna and tell her I'll be ten minutes late." } });
    receive({ kind: "state", data: { state: "thinking" } }); await sleep(1500);
    receive({ kind: "state", data: { state: "confirm" } });
    receive({ kind: "permission", data: { question: "I'd like to run an AppleScript that controls apps on your Mac. Shall I proceed? Say yes, proceed, or no.", tool: "Mac automation: execute script" } });
    await sleep(3500);
    receive({ kind: "permission_done", data: { allowed: true } });
    receive({ kind: "state", data: { state: "speaking" } });
    receive({ kind: "said", data: { text: "Very good. Done — Anna knows you're running ten minutes late." } }); await sleep(3000);
  }
}

/* ------------------------------------------------------------------ start */
resize();
tickClocks();
setInterval(tickClocks, 250);
requestAnimationFrame(draw);
setState("boot");
if (DEMO) runDemo(); else connect();
window.hud = { receive, command };
