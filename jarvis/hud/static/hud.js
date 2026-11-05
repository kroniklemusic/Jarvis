/* J.A.R.V.I.S. heads-up display: everything you see and press.
   Receives live events from JARVIS (server-sent events) and sends commands back.
   Open with ?demo=1 to see it without JARVIS running; add
   &scene=boot|standby|muted|listening|transcribing|thinking|speaking|confirm|settings to freeze a moment. */
"use strict";

const params = new URLSearchParams(location.search);
const TOKEN = params.get("token") || "";
const DEMO = params.has("demo") || !TOKEN;
const $ = (id) => document.getElementById(id);

function loadPref(key, fallback) { try { return localStorage.getItem(`jarvis.${key}`) || fallback; } catch (e) { return fallback; } }
function savePref(key, value) { try { localStorage.setItem(`jarvis.${key}`, value); } catch (e) { /* private mode */ } }

// Shared with scene.js / reactor2d.js, which draw the core from it.
const S = (window.HUD_SCENE = window.HUD_SCENE || { state: "boot", levels: { mic: 0, out: 0 }, boot: 0 });
S.quality = loadPref("quality", "full");

const LABELS = {
  boot: ["Initialising", "Bringing systems online"],
  standby: ["Standing by", ""],
  muted: ["Standing by", "Microphone off · type below, or press Talk"],
  listening: ["Listening", "Speak now"],
  transcribing: ["Transcribing", "Understanding your words"],
  thinking: ["Processing", "Consulting Claude"],
  speaking: ["Responding", ""],
  confirm: ["Authorisation", "Awaiting your decision"],
};
const QUICK_ACTIONS = [
  ["Weather", "What's the weather like today?"],
  ["My day", "What's on my calendar today?"],
  ["Headlines", "Give me the top news headlines."],
  ["Unread email", "Do I have any unread emails?"],
  ["What can you do?", "What can you do for me?"],
];

const ui = {
  state: "boot", followUp: false, ready: false, muted: false, voice: true, hotkey: "Control + Option + J",
  settings: null, options: null, saving: false, entries: 0, jarvisEntry: null, toolRow: null,
  confirmTimeout: 15, confirmTimer: null, net: { down: [], up: [] }, uptime: null, wake: 0.5,
};

/* ------------------------------------------------------------------ helpers */
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;          // never innerHTML: replies may contain web text
  return e;
}
const pad = (n) => String(n).padStart(2, "0");
const hhmm = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
const fmtDuration = (s) => `${pad(Math.floor(s / 3600))}:${pad(Math.floor(s / 60) % 60)}:${pad(Math.floor(s % 60))}`;
function fmtRate(bps) {
  if (bps == null) return "--";
  if (bps >= 1e6) return `${(bps / 1e6).toFixed(1)} MB/s`;
  if (bps >= 1e3) return `${Math.round(bps / 1e3)} KB/s`;
  return `${Math.round(bps)} B/s`;
}
const titleCase = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);

function toast(text, ms = 3200) {
  const t = el("div", "toast", text);
  $("toasts").appendChild(t);
  setTimeout(() => t.classList.add("out"), ms);
  setTimeout(() => t.remove(), ms + 600);
}

/* ------------------------------------------------------------------ comms log */
const log = $("log");
function scrollLog() { log.scrollTop = log.scrollHeight; }
function showEmpty() {
  if (log.children.length) return;
  const e = el("div", "empty");
  e.append(el("b", null, "Comms open"), 'Say "hey Jarvis", press Space, or type a command below.');
  log.appendChild(e);
}
function addEntry(cls) {
  const empty = log.querySelector(".empty");
  if (empty) empty.remove();
  const entry = el("div", `entry ${cls}`);
  log.appendChild(entry);
  while (log.children.length > 250) log.firstChild.remove();
  return entry;
}
function countEntry() { $("comms-count").textContent = String(++ui.entries).padStart(3, "0"); }

function speech(who, text, { instant = false, typed = false } = {}) {
  finishTool();
  const entry = addEntry(`${who}${typed ? " typed" : ""}`);
  const meta = el("div", "meta");
  meta.append(el("span", "who", who === "you" ? "You" : "J.A.R.V.I.S."), el("span", "ts", hhmm(new Date())));
  const p = el("p");
  entry.append(meta, p);
  if (who === "jarvis") {
    const copy = el("button", "copy", "Copy");
    copy.type = "button";
    copy.addEventListener("click", () => {
      const done = () => { copy.textContent = "Copied"; setTimeout(() => (copy.textContent = "Copy"), 1500); };
      if (navigator.clipboard) navigator.clipboard.writeText(p.textContent).then(done, () => {});
    });
    entry.appendChild(copy);
  }
  countEntry();
  typeInto(p, text, instant || who === "you");
  scrollLog();
  return p;
}
function line(cls, text) {
  const entry = addEntry(cls);
  entry.textContent = text;
  scrollLog();
  return entry;
}
function startTool(label) {
  finishTool();
  const row = addEntry("tool");
  row.append(el("i", "spin"), el("span", null, label));
  ui.toolRow = row;
  scrollLog();
}
function finishTool() {
  if (ui.toolRow) { ui.toolRow.classList.add("done"); ui.toolRow = null; }
}

// Typewriter, one job per paragraph so sentences never interleave.
const typing = [];
function typeInto(p, text, instant) {
  const job = typing.find((j) => j.p === p);
  if (job) { job.text += ` ${text}`; return; }
  const lead = p.textContent ? " " : "";
  if (instant) { p.textContent += lead + text; return; }
  typing.push({ p, text: lead + text, i: 0 });
  p.classList.add("caret");
}
function tickTyping() {
  for (let k = typing.length - 1; k >= 0; k--) {
    const job = typing[k];
    const step = Math.max(1, Math.ceil(job.text.length / 50));
    job.p.textContent += job.text.slice(job.i, job.i + step);
    job.i += step;
    if (job.i >= job.text.length) { job.p.classList.remove("caret"); typing.splice(k, 1); }
  }
  if (typing.length) scrollLog();
  requestAnimationFrame(tickTyping);
}

let captionTimer = null;
function caption(text) {
  const c = $("caption");
  c.classList.remove("fade");
  c.textContent = text;
  clearTimeout(captionTimer);
  captionTimer = setTimeout(() => c.classList.add("fade"), 7000);
}

/* ------------------------------------------------------------------ state */
function sceneState() { return ui.muted && ui.state === "standby" ? "muted" : ui.state; }
function setState(state, followUp) {
  ui.state = state;
  ui.followUp = !!followUp;
  const shown = sceneState();
  S.state = shown;
  document.body.className = `state-${shown}`;
  const [name, sub] = LABELS[shown] || LABELS.standby;
  $("state-name").textContent = name;
  let detail = sub;
  if (shown === "standby") detail = `Say "hey Jarvis" · press Space · or type below`;
  if (shown === "listening" && followUp) detail = "Go ahead · no wake word needed";
  $("state-sub").textContent = detail;
  if (["standby", "listening", "muted"].includes(shown)) { finishTool(); ui.jarvisEntry = null; }
  if (shown === "speaking") finishTool();
  setRow("r-link", shown === "thinking" ? "busy" : ui.claudeOnline === false ? "down" : ui.ready ? "ok" : "", null);
}
function setRow(id, status, text) {
  const dd = $(id);
  if (!dd) return;
  dd.className = status || "";
  if (text != null) dd.querySelector("span").textContent = text;
}
function setToggles(d) {
  if ("muted" in d) ui.muted = !!d.muted;
  if ("voice_replies" in d) ui.voice = !!d.voice_replies;
  const mic = $("pill-mic"), voice = $("pill-voice");
  mic.className = `status ${ui.muted ? "off" : "on"}`;
  mic.setAttribute("aria-pressed", String(ui.muted));
  $("pill-mic-text").textContent = ui.muted ? "Mic off" : "Mic live";
  voice.className = `status ${ui.voice ? "on" : "off"}`;
  voice.setAttribute("aria-pressed", String(ui.voice));
  $("pill-voice-text").textContent = ui.voice ? "Voice on" : "Voice off";
  $("set-voice-replies").checked = ui.voice;
  setRow("r-wake", ui.muted ? "down" : "ok", ui.muted ? "Microphone off" : `Armed · ${sensitivity(ui.wake)}%`);
  setState(ui.state, ui.followUp);
}

/* ------------------------------------------------------------------ boot */
function bootProgress(d) {
  document.querySelectorAll("#boot-steps li").forEach((li) => {
    const n = Number(li.dataset.step);
    li.className = d.ready || n < d.step ? "done" : n === d.step ? "busy" : "";
  });
  S.boot = d.ready ? 1 : (d.step - 0.5) / d.total;
  $("boot-bar").style.width = `${Math.round(S.boot * 100)}%`;
  if (d.ready && !ui.ready) {
    ui.ready = true;
    setTimeout(() => $("boot").classList.add("hidden"), 900);
    if (ui.state === "boot") setState("standby");
    showEmpty();
  }
}
function bootLine(text) {
  const lines = $("boot-lines");
  lines.appendChild(el("div", null, `› ${text.trim()}`));
  while (lines.children.length > 6) lines.firstChild.remove();
}

/* ------------------------------------------------------------------ panels */
const TOOL_NAMES = { "macos-automator": "Mac control", playwright: "Browser", duckduckgo: "Web search", "google-workspace": "Google" };
function readout(name, value) {             // small live figures printed on the 3D instrument
  S.readouts = { ...(S.readouts || {}), [name]: value };
}
function applyConfig(d) {
  readout("core", (d.model || "sonnet").toUpperCase());
  $("r-model").textContent = `Claude · ${titleCase(d.model || "sonnet")}`;
  const where = d.voice_device === "cpu" ? " · CPU" : d.voice_device === "mps" ? " · GPU" : "";
  const voiceName = (d.voice_detail || "").replace(/^[ab][mf]_/, "");
  $("r-voice").textContent = `${titleCase(d.voice || "—")}${voiceName ? ` · ${titleCase(voiceName)}` : ""}${where}`;
  $("r-speech").textContent = (d.speech_model || "—").replace(/^mlx-community\//, "").replace(/-mlx$/, "");
  if (d.hotkey) ui.hotkey = d.hotkey;
  if (d.confirm_timeout_seconds) ui.confirmTimeout = d.confirm_timeout_seconds;
  // Macs with 8 GB or less start in Lite mode (unless you chose a mode in Settings),
  // leaving more of the machine for speech recognition and the voice.
  if (d.memory_gb && d.memory_gb <= 8.5 && !loadPref("quality", "")) S.quality = "lite";
  S.lowMemory = !!(d.memory_gb && d.memory_gb <= 8.5);
  const tools = $("tools");
  tools.textContent = "";
  (d.tools || []).forEach((t) => {
    const li = el("li", null, TOOL_NAMES[t] || t);
    li.dataset.tool = t;
    tools.appendChild(li);
  });
  if (!tools.children.length) tools.appendChild(el("li", "muted", "None"));
}
function flashTool(name) {
  const server = (name || "").split("__")[1];
  const li = server && document.querySelector(`#tools li[data-tool="${CSS.escape(server)}"]`);
  if (li) { li.classList.add("active"); setTimeout(() => li.classList.remove("active"), 3000); }
}
function gauge(id, fraction, text, label) {
  const g = $(id);
  if (!g) return;
  const f = fraction == null ? 0 : Math.max(0, Math.min(1, fraction));
  g.querySelector(".value").style.strokeDashoffset = String(263.9 * (1 - f));
  g.classList.toggle("warn", fraction != null && f > 0.75 && f <= 0.9);
  g.classList.toggle("hot", fraction != null && f > 0.9);
  g.querySelector("b").textContent = text;
  if (label) g.querySelector("span").textContent = label;
}
function bar(id, fraction, text) {
  const b = $(id);
  b.style.width = `${Math.round(Math.max(0, Math.min(1, fraction || 0)) * 100)}%`;
  b.classList.toggle("hot", (fraction || 0) > 0.9);
  $(`${id}-v`).textContent = text;
}
function spark(id, values, max) {
  const n = values.length;
  const pts = values.map((v, i) => `${((i / Math.max(1, 59)) * 200).toFixed(1)},${(42 - (v / max) * 38).toFixed(1)}`);
  $(id).setAttribute("points", n ? pts.join(" ") : "");
}
function applyTelemetry(d) {
  if (d.uptime_s != null) ui.uptime = { s: d.uptime_s, at: performance.now() };
  gauge("g-cpu", d.cpu != null ? d.cpu / 100 : null, d.cpu != null ? `${Math.round(d.cpu)}%` : "--");
  readout("cpu", d.cpu != null ? `${Math.round(d.cpu)}%` : "--");
  if (d.memory) readout("mem", `${d.memory.used_gb} GB`);
  if (d.net) readout("net", fmtRate(d.net.down_bps));
  if (d.memory) gauge("g-mem", d.memory.fraction, `${Math.round(d.memory.fraction * 100)}%`, `${d.memory.used_gb}/${Math.round(d.memory.total_gb)} GB`);
  if (d.battery) {
    gauge("g-bat", d.battery.percent / 100, `${d.battery.percent}%`, d.battery.charging ? "Charging" : "Battery");
    // a full battery is good news: only a low, discharging one is flagged
    $("g-bat").classList.toggle("warn", d.battery.percent > 10 && d.battery.percent <= 20 && !d.battery.charging);
    $("g-bat").classList.toggle("hot", d.battery.percent <= 10 && !d.battery.charging);
  } else {
    gauge("g-bat", 1, "AC", "Power");
    $("g-bat").classList.remove("warn", "hot");
  }
  if (d.net) {
    ui.net.down.push(d.net.down_bps);
    ui.net.up.push(d.net.up_bps);
    if (ui.net.down.length > 60) { ui.net.down.shift(); ui.net.up.shift(); }
    const max = Math.max(50e3, ...ui.net.down, ...ui.net.up);
    spark("spark-down", ui.net.down, max);
    spark("spark-up", ui.net.up, max);
    $("net-down").textContent = fmtRate(d.net.down_bps);
    $("net-up").textContent = fmtRate(d.net.up_bps);
  }
  if (d.disk) bar("m-disk", d.disk.fraction, `${Math.round(d.disk.free_gb)} GB`);
  ui.claudeOnline = d.claude_online;
  setRow("r-link", d.claude_online === false ? "down" : ui.state === "thinking" ? "busy" : "ok",
    d.claude_online === false ? "Reconnecting" : "Online");
  if (d.swap_gb >= 0.5) $("r-speech").title = `${d.swap_gb.toFixed(1)} GB of memory has overflowed to disk`;
}
function showLatency(d) {
  const box = $("latency");
  box.textContent = "Latency  ";
  const parts = [["Speech", d.typed ? null : d.transcribe_s], ["Claude", d.claude_s], ["Voice", d.voice_s]].filter(([, v]) => typeof v === "number");
  parts.forEach(([label, value], i) => {
    if (i) box.append("  ·  ");
    box.append(`${label} `);
    box.appendChild(el("b", null, `${value.toFixed(1)}s`));
  });
  if (d.behind_s >= 0.5) box.append(`  ·  ${d.behind_s.toFixed(1)}s behind`);
  const last = [...log.querySelectorAll(".entry.jarvis")].pop();
  if (last && !last.querySelector(".timing") && parts.length) {
    last.appendChild(el("div", "timing", parts.map(([l, v]) => `${l.toLowerCase()} ${v.toFixed(1)}s`).join(" · ")));
  }
}

/* ------------------------------------------------------------------ authorisation */
function showConfirm(d) {
  $("confirm-text").textContent = d.question || "";
  $("confirm-tool").textContent = d.tool ? `Requested · ${d.tool}` : "";
  $("confirm").classList.remove("hidden");
  const total = ui.confirmTimeout + 6;           // the question is spoken first, then JARVIS listens
  const started = performance.now();
  clearInterval(ui.confirmTimer);
  const tick = () => {
    const left = Math.max(0, total - (performance.now() - started) / 1000);
    $("countdown-num").textContent = String(Math.ceil(left));
    $("countdown-ring").style.strokeDashoffset = String(276.5 * (1 - left / total));
    if (left <= 0) clearInterval(ui.confirmTimer);
  };
  tick();
  ui.confirmTimer = setInterval(tick, 250);
  if (!document.hasFocus()) postToApp("attention");
  $("btn-yes").focus({ preventScroll: true });
}
function hideConfirm() {
  $("confirm").classList.add("hidden");
  clearInterval(ui.confirmTimer);
}
function showFatal(text) { $("fatal-text").textContent = text; $("fatal").classList.remove("hidden"); }

/* ------------------------------------------------------------------ settings drawer */
// Wake sensitivity is shown as 0-100% in steps of 5 (higher % = a lower, easier threshold).
const sensitivity = (threshold) => Math.round(((0.9 - threshold) / 0.7) * 20) * 5;
const thresholdFrom = (pct) => Math.round((0.9 - (pct / 100) * 0.7) * 100) / 100;
function selectSegment(group, value) {
  group.querySelectorAll("button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.value === value)));
}
function segmentValue(group) {
  const b = group.querySelector('button[aria-checked="true"]');
  return b ? b.dataset.value : null;
}
function renderSettings() {
  const v = ui.settings, o = ui.options;
  if (!v || !o) return;
  const seg = $("set-model");
  seg.textContent = "";
  o.models.forEach((m) => {
    const b = el("button", null, m.label.split(" · ")[0]);
    b.type = "button"; b.dataset.value = m.value; b.title = m.label; b.setAttribute("role", "radio");
    b.addEventListener("click", () => selectSegment(seg, m.value));
    seg.appendChild(b);
  });
  selectSegment(seg, v.model);
  const sel = $("set-voice");
  sel.textContent = "";
  o.voices.forEach((x) => { const opt = el("option", null, x.label); opt.value = x.value; sel.appendChild(opt); });
  sel.value = v.kokoro_voice;
  $("set-speed").value = v.speech_speed;
  $("set-wake").value = sensitivity(v.wake_threshold);
  $("set-follow").value = v.follow_up_seconds;
  $("set-timings").checked = !!v.show_timings;
  $("set-voice-replies").checked = ui.voice;
  selectSegment($("set-quality"), S.quality);
  updateSettingLabels();
  ui.formStart = formValues();      // only settings you actually change get saved
}
function updateSettingLabels() {
  $("set-speed-v").textContent = `${Number($("set-speed").value).toFixed(2)}×`;
  $("set-wake-v").textContent = `${$("set-wake").value}%`;
  const f = Number($("set-follow").value);
  $("set-follow-v").textContent = f ? `${f} s` : "Off";
}
["set-speed", "set-wake", "set-follow"].forEach((id) => $(id).addEventListener("input", updateSettingLabels));
$("set-quality").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => selectSegment($("set-quality"), b.dataset.value)));
function formValues() {
  return {
    model: segmentValue($("set-model")),
    kokoro_voice: $("set-voice").value,
    speech_speed: Number($("set-speed").value),
    wake: Number($("set-wake").value),
    follow_up_seconds: Number($("set-follow").value),
    show_timings: $("set-timings").checked,
  };
}
function changedSettings() {
  const start = ui.formStart || {}, now = formValues(), out = {};
  for (const [k, val] of Object.entries(now)) {
    if (val === start[k] || val == null || val === "" || val !== val) continue;
    if (k === "wake") out.wake_threshold = thresholdFrom(val);
    else out[k] = val;
  }
  return out;
}
function openSettings() {
  renderSettings();
  $("drawer").classList.add("open");
  $("drawer").setAttribute("aria-hidden", "false");
}
function closeSettings() {
  $("drawer").classList.remove("open");
  $("drawer").setAttribute("aria-hidden", "true");
}
function saveSettings() {
  const quality = segmentValue($("set-quality")) || "full";
  if (quality !== S.quality) { S.quality = quality; savePref("quality", quality); }
  const changes = changedSettings();
  if (!Object.keys(changes).length) { closeSettings(); toast("Settings saved"); return; }
  ui.saving = true;
  command("settings", { values: changes });
}
$("btn-settings").addEventListener("click", () => ($("drawer").classList.contains("open") ? closeSettings() : openSettings()));
$("btn-close-settings").addEventListener("click", closeSettings);
$("btn-cancel-settings").addEventListener("click", closeSettings);
$("btn-save-settings").addEventListener("click", saveSettings);
$("set-voice-replies").addEventListener("change", (e) => command(e.target.checked ? "voice_on" : "voice_off"));

/* ------------------------------------------------------------------ events from JARVIS */
function receive(ev) {
  const d = ev.data || {};
  const instant = ev.t && Date.now() / 1000 - ev.t > 3;     // replayed history: no animation
  switch (ev.kind) {
    case "state": setState(d.state, d.follow_up); break;
    case "level":
      if ("mic" in d) S.levels.mic = d.mic;
      if ("out" in d) S.levels.out = d.out;
      break;
    case "heard":
      speech("you", d.text, { instant, typed: d.typed });
      ui.jarvisEntry = null;
      if (!instant) caption(`“${d.text}”`);
      break;
    case "said":
      if (ui.jarvisEntry && ui.jarvisEntry.isConnected) { finishTool(); typeInto(ui.jarvisEntry, d.text, instant); }
      else ui.jarvisEntry = speech("jarvis", d.text, { instant });
      if (!instant) caption(d.text);
      break;
    case "tool":
      startTool(d.label || d.name);
      if (instant) finishTool(); else flashTool(d.name);
      break;
    case "notice":
      if (ui.ready) line("sys", d.text); else bootLine(d.text);
      break;
    case "boot": bootProgress(d); break;
    case "config": applyConfig(d); break;
    case "settings":
      ui.settings = d.values; ui.options = d.options;
      if ("wake_threshold" in (d.values || {})) { ui.wake = d.values.wake_threshold; setToggles({}); }
      if (ui.saving) { ui.saving = false; closeSettings(); toast("Settings saved"); }
      else if (!$("drawer").classList.contains("open")) renderSettings();
      break;
    case "toggles": setToggles(d); break;
    case "telemetry": applyTelemetry(d); break;
    case "permission":
      ui.jarvisEntry = null;
      line("perm", `Authorisation requested · ${d.tool || "action"}`);
      if (!instant) showConfirm(d);
      break;
    case "permission_done":
      hideConfirm();
      line(d.allowed ? "perm ok" : "perm", d.allowed ? "Authorised" : "Denied");
      break;
    case "timing": showLatency(d); break;
    case "cleared":
      log.textContent = ""; ui.entries = 0; ui.jarvisEntry = null; ui.toolRow = null;
      $("comms-count").textContent = "000"; caption(""); showEmpty();
      if (!instant) toast("New conversation started");
      break;
    case "fatal": showFatal(d.text); break;
  }
}

function setLink(status) {
  const pill = $("pill-link");
  pill.className = `status static ${status === "Online" || status === "Demo" ? "on" : status === "Link lost" ? "lost" : ""}`;
  $("pill-link-text").textContent = status;
}

function connect() {
  const source = new EventSource(`/events?token=${encodeURIComponent(TOKEN)}`);
  source.onopen = () => setLink("Online");
  source.onmessage = (e) => { try { receive(JSON.parse(e.data)); } catch (err) { console.error(err); } };
  source.onerror = () => setLink("Link lost");
}

function command(name, payload) {
  if (DEMO) return demoCommand(name, payload || {});
  fetch(`/command?token=${encodeURIComponent(TOKEN)}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ command: name, ...(payload || {}) }),
  }).then((r) => { if (!r.ok && name === "settings") { ui.saving = false; toast("Those settings couldn't be saved"); } })
    .catch(() => setLink("Link lost"));
}

function postToApp(action) {
  try { window.webkit.messageHandlers.jarvis.postMessage({ action }); return true; } catch (e) { return false; }
}

/* ------------------------------------------------------------------ controls */
const input = $("command-input");
$("command-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  command("ask", { text });
  input.value = "";
});
$("btn-talk").addEventListener("click", () => command("talk"));
$("btn-stop").addEventListener("click", () => command("stop"));
$("btn-yes").addEventListener("click", () => command("yes"));
$("btn-no").addEventListener("click", () => command("no"));
$("btn-new").addEventListener("click", () => command("new_conversation"));
$("pill-mic").addEventListener("click", () => command(ui.muted ? "unmute" : "mute"));
$("pill-voice").addEventListener("click", () => command(ui.voice ? "voice_off" : "voice_on"));
$("btn-fullscreen").addEventListener("click", () => {
  if (postToApp("fullscreen")) return;
  if (document.fullscreenElement) document.exitFullscreen(); else document.documentElement.requestFullscreen().catch(() => {});
});
window.addEventListener("jarvis-core-click", () => command("talk"));
QUICK_ACTIONS.forEach(([label, text]) => {
  const chip = el("button", "chip", label);
  chip.type = "button";
  chip.title = text;
  chip.addEventListener("click", () => command("ask", { text }));
  $("chips").appendChild(chip);
});

document.addEventListener("keydown", (e) => {
  const tag = (e.target.tagName || "").toLowerCase();
  const typing = tag === "input" || tag === "select" || tag === "textarea";
  const confirming = !$("confirm").classList.contains("hidden");
  const drawerOpen = $("drawer").classList.contains("open");
  if ((e.metaKey || e.ctrlKey) && e.key === ",") { e.preventDefault(); openSettings(); return; }
  if (e.metaKey || e.ctrlKey || e.altKey || e.repeat) return;
  if (e.key === "Escape") {
    e.preventDefault();
    if (drawerOpen) closeSettings();
    else if (typing && input.value) input.value = "";
    else if (typing) input.blur();
    else command("stop");
    return;
  }
  if (typing) return;
  if (confirming && e.key.toLowerCase() === "y") { e.preventDefault(); command("yes"); return; }
  if (confirming && e.key.toLowerCase() === "n") { e.preventDefault(); command("no"); return; }
  if (e.code === "Space" && tag !== "button") { e.preventDefault(); command("talk"); return; }
  if (e.key === "/") { e.preventDefault(); input.focus(); return; }
  if (e.key === ",") { e.preventDefault(); openSettings(); }
});

/* ------------------------------------------------------------------ clock, uptime, meters */
function tick() {
  const now = new Date();
  $("clock-time").textContent = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
  $("clock-date").textContent = now.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
  if (ui.uptime) $("uptime").textContent = fmtDuration(ui.uptime.s + (performance.now() - ui.uptime.at) / 1000);
}
function meters() {
  const pct = (v) => Math.min(1, Math.sqrt(Math.max(0, v || 0)) * 2.6);
  const m = ui.muted ? 0 : pct(S.levels.mic), o = pct(S.levels.out);
  bar("m-mic", m, ui.muted ? "Off" : `${Math.round(m * 100)}%`);
  bar("m-out", o, `${Math.round(o * 100)}%`);
}

/* ------------------------------------------------------------------ where the core should sit */
// The core is drawn in the free space between the panels, above the status label.
const stage = document.querySelector(".stage");
function measureStage() {
  const r = stage.getBoundingClientRect();
  const label = document.querySelector(".state-block").getBoundingClientRect().height;
  const h = Math.max(0, r.height - label);
  S.view = { x: r.left + r.width / 2, y: r.top + h / 2, w: r.width, h };
}
window.addEventListener("resize", measureStage);
if (window.ResizeObserver) new ResizeObserver(measureStage).observe(stage);
measureStage();

/* ------------------------------------------------------------------ 3D core (with a 2D backup) */
let fallbackStarted = false;
function startFallback(reason) {
  if (fallbackStarted || window.HUD_SCENE_READY) return;
  fallbackStarted = true;
  console.warn("3D display unavailable, using 2D:", reason);
  $("scene").hidden = true;
  $("scene2d").hidden = false;
  window.startReactor2D($("scene2d"));
}
window.addEventListener("jarvis-scene-failed", (e) => startFallback(e.detail));
setTimeout(() => { if (!window.HUD_SCENE_STARTED && !window.HUD_SCENE_READY) startFallback("3D module didn't load"); }, 8000);

/* ------------------------------------------------------------------ demo mode */
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const DEMO_OPTIONS = {
  models: [{ value: "haiku", label: "Haiku · fastest" }, { value: "sonnet", label: "Sonnet · balanced" }, { value: "opus", label: "Opus · most capable" }],
  voices: [{ value: "bm_george", label: "George · British" }, { value: "bm_fable", label: "Fable · British" }, { value: "bf_emma", label: "Emma · British" }, { value: "am_michael", label: "Michael · American" }],
  ranges: { speech_speed: [0.7, 1.5], wake_threshold: [0.2, 0.9], follow_up_seconds: [0, 20] },
};
const DEMO_SETTINGS = { model: "sonnet", kokoro_voice: "bm_george", speech_speed: 1.0, wake_threshold: 0.5, follow_up_seconds: 8, show_timings: true };
const DEMO_CONFIG = {
  model: "sonnet", voice: "kokoro", voice_detail: "bm_george", voice_device: "cpu", speech_model: "mlx-community/whisper-small-mlx",
  hotkey: "Control + Option + J", tools: ["macos-automator", "playwright", "duckduckgo"], memory_gb: 16, confirm_timeout_seconds: 15,
};
const demo = { busy: false };
function demoTelemetry() {
  const t = performance.now() / 1000;
  receive({ kind: "telemetry", data: {
    uptime_s: 3725 + Math.round(t), cpu: 18 + 12 * Math.abs(Math.sin(t / 3)), cores: 8, swap_gb: 0,
    memory: { used_gb: 9.4, total_gb: 16, fraction: 0.59 }, battery: { percent: 82, charging: false, on_power: false },
    net: { down_bps: 40e3 + 900e3 * Math.abs(Math.sin(t / 2.3)) ** 3, up_bps: 12e3 + 80e3 * Math.abs(Math.sin(t / 1.7)) ** 4 },
    disk: { free_gb: 312, total_gb: 494, fraction: 0.37 }, claude_online: true, behind_s: 0,
  } });
}
function demoLevels() {
  const t = performance.now() / 1000;
  const v = 0.012 + 0.03 * Math.abs(Math.sin(t * 3.1) * Math.sin(t * 7.3));
  S.levels.mic = ui.state === "listening" || ui.state === "confirm" ? v : 0.0008;
  S.levels.out = ui.state === "speaking" ? v * 1.5 : 0;
}
async function demoAnswer(question, sentences, tool) {
  demo.busy = true;
  receive({ kind: "heard", data: { text: question, typed: true } });
  receive({ kind: "state", data: { state: "thinking" } });
  await sleep(900);
  if (tool) { receive({ kind: "tool", data: tool }); await sleep(1300); }
  receive({ kind: "state", data: { state: "speaking" } });
  for (const s of sentences) { receive({ kind: "said", data: { text: s } }); await sleep(Math.max(1600, s.length * 55)); }
  receive({ kind: "timing", data: { transcribe_s: 0.0, typed: true, claude_s: 1.6, voice_s: 0.6 } });
  receive({ kind: "state", data: { state: "listening", follow_up: true } });
  await sleep(2500);
  receive({ kind: "state", data: { state: "standby" } });
  demo.busy = false;
}
function demoCommand(name, payload) {
  if (name === "ask" && !demo.busy) demoAnswer(payload.text, ["I'm afraid I'm only a demonstration at the moment.", "Run JARVIS for real and I'll answer properly."]);
  if (name === "yes" || name === "no") receive({ kind: "permission_done", data: { allowed: name === "yes" } });
  if (name === "stop") receive({ kind: "state", data: { state: "standby" } });
  if (name === "talk") receive({ kind: "state", data: { state: "listening" } });
  if (name === "mute" || name === "unmute") receive({ kind: "toggles", data: { muted: name === "mute" } });
  if (name === "voice_on" || name === "voice_off") receive({ kind: "toggles", data: { voice_replies: name === "voice_on" } });
  if (name === "new_conversation") receive({ kind: "cleared", data: {}, t: Date.now() / 1000 });
  if (name === "settings") receive({ kind: "settings", data: { values: { ...ui.settings, ...payload.values }, options: DEMO_OPTIONS } });
}
function demoHistory() {
  const old = Date.now() / 1000 - 600;
  [["heard", { text: "Good morning. What's on my calendar today?" }],
   ["tool", { name: "mcp__macos-automator__execute_script", label: "Mac automation: execute script" }],
   ["said", { text: "Good morning. You have two engagements today: a design review at eleven, and dinner with Anna at seven." }],
   ["timing", { transcribe_s: 0.5, claude_s: 1.9, voice_s: 0.6 }],
  ].forEach(([kind, data]) => receive({ kind, data, t: old }));
}
async function runDemo() {
  setLink("Demo");
  setInterval(demoLevels, 50);
  const scene = params.get("scene");
  const steps = ["Wake word", "Speech detector", "Speech recognition", "Voice synthesis"];
  if (scene === "boot") {
    receive({ kind: "boot", data: { step: 3, total: 4, label: steps[2] } });
    ["Starting JARVIS on an Apple Silicon Mac.", "Loading models (about 20 seconds)...", "1/4  Wake word", "2/4  Speech detector", "3/4  Speech recognition"].forEach(bootLine);
    return;
  }
  for (let s = 1; s <= 4; s++) {
    receive({ kind: "boot", data: { step: s, total: 4, label: steps[s - 1] } });
    bootLine(`${s}/4  ${steps[s - 1]}`);
    if (!scene) await sleep(800);
  }
  receive({ kind: "config", data: DEMO_CONFIG });
  receive({ kind: "settings", data: { values: DEMO_SETTINGS, options: DEMO_OPTIONS } });
  receive({ kind: "toggles", data: { muted: false, voice_replies: true } });
  demoTelemetry();
  setInterval(demoTelemetry, 2000);
  receive({ kind: "boot", data: { step: 4, total: 4, label: "Ready", ready: true } });
  if (scene) {
    $("boot").classList.add("hidden");
    demoHistory();
    if (scene === "standby" || scene === "settings") receive({ kind: "state", data: { state: "standby" } });
    if (scene === "settings") openSettings();
    if (scene === "muted") receive({ kind: "toggles", data: { muted: true } });
    if (scene === "listening") receive({ kind: "state", data: { state: "listening" } });
    if (scene === "transcribing") receive({ kind: "state", data: { state: "transcribing" } });
    if (scene === "thinking") {
      receive({ kind: "heard", data: { text: "Find me a good Italian restaurant near Covent Garden." } });
      receive({ kind: "tool", data: { name: "mcp__duckduckgo__search", label: "DuckDuckGo: search" } });
      receive({ kind: "state", data: { state: "thinking" } });
    }
    if (scene === "speaking") {
      receive({ kind: "heard", data: { text: "What's the weather like in London tomorrow?" } });
      receive({ kind: "tool", data: { name: "mcp__duckduckgo__search", label: "DuckDuckGo: search" } });
      receive({ kind: "said", data: { text: "Tomorrow looks mild in London, around fourteen degrees." } });
      receive({ kind: "said", data: { text: "Expect light rain from mid-afternoon, so an umbrella would be wise." } });
      receive({ kind: "state", data: { state: "speaking" } });
    }
    if (scene === "confirm") {
      receive({ kind: "heard", data: { text: "Email Anna and tell her I'll be ten minutes late." } });
      receive({ kind: "state", data: { state: "confirm" } });
      receive({ kind: "permission", data: { question: "I'd like to run an AppleScript that controls apps on your Mac. Shall I proceed? Say yes, proceed, or no.", tool: "Mac automation: execute script" } });
    }
    return;
  }
  demoHistory();
  for (;;) {
    receive({ kind: "state", data: { state: "standby" } }); await sleep(3000);
    if (demo.busy) { await sleep(4000); continue; }
    receive({ kind: "state", data: { state: "listening" } }); await sleep(2600);
    receive({ kind: "state", data: { state: "transcribing" } }); await sleep(700);
    receive({ kind: "heard", data: { text: "What's the weather like in London tomorrow?" } });
    receive({ kind: "state", data: { state: "thinking" } }); await sleep(900);
    receive({ kind: "tool", data: { name: "mcp__duckduckgo__search", label: "DuckDuckGo: search" } }); await sleep(1300);
    receive({ kind: "state", data: { state: "speaking" } });
    receive({ kind: "said", data: { text: "Tomorrow looks mild in London, around fourteen degrees." } }); await sleep(2600);
    receive({ kind: "said", data: { text: "Expect light rain from mid-afternoon, so an umbrella would be wise." } }); await sleep(3200);
    receive({ kind: "timing", data: { transcribe_s: 0.5, claude_s: 1.8, voice_s: 0.6 } });
    receive({ kind: "state", data: { state: "listening", follow_up: true } }); await sleep(2400);
    receive({ kind: "state", data: { state: "transcribing" } }); await sleep(600);
    receive({ kind: "heard", data: { text: "Email Anna and tell her I'll be ten minutes late." } });
    receive({ kind: "state", data: { state: "thinking" } }); await sleep(1500);
    receive({ kind: "state", data: { state: "confirm" } });
    receive({ kind: "permission", data: { question: "I'd like to run an AppleScript that controls apps on your Mac. Shall I proceed? Say yes, proceed, or no.", tool: "Mac automation: execute script" } });
    await sleep(3500);
    receive({ kind: "permission_done", data: { allowed: true } });
    receive({ kind: "state", data: { state: "speaking" } });
    receive({ kind: "said", data: { text: "Very good. Done: Anna knows you're running ten minutes late." } }); await sleep(3000);
  }
}

/* ------------------------------------------------------------------ start */
setState("boot");
setToggles({});
tick();
setInterval(tick, 250);
setInterval(meters, 100);
requestAnimationFrame(tickTyping);
if (DEMO) runDemo(); else connect();
window.hud = { receive, command };
