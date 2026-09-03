/* ryujin-lcd web panel. Talks to ryujin_lcd/web.py over /api/*. No dependencies. */
(() => {
"use strict";
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const SLOTS = 16;
const KIND_LABEL = { gif: "Animation", jpg: "Wallpaper", clock: "Clock", hwmon: "Hardware Monitor", slideshow: "Slideshow" };
const UNIT_PREVIEW = [["℃", "°C"], ["↌", " RPM"], ["↊", " V"]];

const state = {
  status: null, sensors: [], config: null,
  form: null,           // what the Display panel will apply
  settings: null,       // brightness / standby form
  settingsDirty: false,
  panel: "display",
  images: {},           // "gif-3" -> HTMLImageElement
  clockTimer: null,
};

// ---- helpers ----------------------------------------------------------------------
async function api(method, path, body, raw) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    if (raw) opt.body = body;
    else { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  }
  const r = await fetch(path, opt);
  let data = {};
  try { data = await r.json(); } catch (e) { /* no body */ }
  if (!r.ok) throw new Error(data.error || `${r.status} ${r.statusText}`);
  return data;
}
function toast(msg, kind = "ok", ms = 3500) {
  const el = document.createElement("div");
  el.className = `toast ${kind}`; el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), ms);
}
const clone = (o) => JSON.parse(JSON.stringify(o));
const hex6 = (h) => "#" + String(h || "000000").replace("#", "").slice(0, 6).padEnd(6, "0").toLowerCase();
const fromHex6 = (h) => h.replace("#", "").toUpperCase();
function glyphs(v) { let s = v; for (const [g, t] of UNIT_PREVIEW) s = s.split(g).join(t); return s; }
function fmtKB(kb) { return kb >= 1024 ? (kb / 1024).toFixed(1) + " MB" : kb + " KB"; }
function fmtBytes(b) { return b == null ? "" : b >= 1048576 ? (b / 1048576).toFixed(1) + " MB" : b >= 1024 ? Math.round(b / 1024) + " KB" : b + " B"; }
function setPct(input) { const min = +input.min || 0, max = +input.max || 100; input.style.setProperty("--pct", ((input.value - min) / (max - min) * 100) + "%"); }

// ---- form model -------------------------------------------------------------------
function formFromConfig(cfg) {
  const hw = cfg.hwmon, ss = cfg.slideshow, bn = ss.banner || {};
  const lines = (hw.lines || []).slice(0, 3).map((l) => ({ label: l.label || "", sensor: l.sensor || "", value: l.value || "" }));
  while (lines.length < 3) lines.push({ label: "", sensor: "", value: "" });
  const rgba = (bn.color || "FFFFFFFF").replace("#", "").padEnd(8, "F");
  return {
    mode: cfg.mode === "slideshow" ? "slideshow" : "hwmon",
    hwmon: { count: Math.min(3, Math.max(1, hw.count || (hw.lines || []).length || 3)), lines,
             bg: (hw.bg || "000000").toUpperCase(), fg: (hw.fg || "FFFFFF").toUpperCase(), live: hw.live !== false, interval: +hw.interval || 2 },
    slideshow: {
      source: ["gif", "jpg", "clock"].includes(ss.source) ? ss.source : "gif",
      gif_slot: storedSlot("gif", ss.gif_slot), jpg_slot: storedSlot("jpg", ss.jpg_slot),
      duration: +ss.duration || 5, h24: !!ss.h24,
      banner: { lines: Array.from({ length: 6 }, (_, i) => (bn.lines || [])[i] || ""), color: rgba.slice(0, 6),
                alpha: parseInt(rgba.slice(6, 8), 16), align: bn.align ? 1 : 0, x: bn.x ?? 8, font: bn.font ?? 3 },
    },
  };
}
function storedSlot(type, slot) {
  if (slot == null) return null;
  const st = state.status && state.status.storage;
  return st && st[type] && st[type].items && !st[type].items[slot].used ? null : slot;
}
function isDirty() { return state.config && JSON.stringify(state.form) !== JSON.stringify(formFromConfig(state.config)); }

// ---- load ---------------------------------------------------------------------------
let statusSeq = 0, sensorSeq = 0;
async function loadStatus(storage = false) {
  const seq = ++statusSeq;
  let st;
  try {
    st = await api("GET", "/api/status" + (storage ? "?storage=1" : ""));
  } catch (e) {
    st = { connected: false, error: e.message, storage: null, config: state.config };
  }
  if (seq !== statusSeq) return;   // a newer poll already answered
  state.status = st;
  if (state.status.config) {
    state.config = state.status.config;
    if (!state.form) { state.form = formFromConfig(state.config); hydrateDisplay(); }
  }
  renderHero(); renderDashboard(); renderMedia(); renderPickers(); renderSettings(); renderAdvanced(); renderApplyBar(); drawPreview();
}
async function loadSensors() {
  const seq = ++sensorSeq;
  try { const s = (await api("GET", "/api/sensors")).sensors || []; if (seq !== sensorSeq) return; state.sensors = s; } catch (e) { /* keep old */ }
  renderSensors(); refreshSensorOptions(); drawPreview();
}

// ---- hero / status -------------------------------------------------------------------
function renderHero() {
  const s = state.status, conn = $("#conn");
  $("#demo-badge").hidden = !s.demo;
  if (s.version) $("#rail-version").textContent = "v" + s.version;
  conn.className = "conn " + (s.connected ? "on" : "off");
  conn.lastElementChild.textContent = s.connected ? (s.demo ? "Simulated" : "Connected") : "Not connected";
  conn.title = s.error || (s.path ? s.path : "");
  $("#m-fw").textContent = s.firmware || "—";
  $("#m-current").textContent = s.current ? describeCurrent(s.current) : "—";
  $("#m-bright").textContent = s.display ? s.display.brightness + "%" : "—";
  $("#m-standby").textContent = s.display ? (s.display.standby ? `On · GIF ${s.display.anim_slot}` : "Off") : "—";
  const st = s.storage;
  if (st && st.total_kb) {
    const used = st.total_kb - st.free_kb;
    $("#storage-text").textContent = `${fmtKB(used)} of ${fmtKB(st.total_kb)} · ${st.gif.used.length} GIF · ${st.jpg.used.length} JPG`;
    $("#storage-bar").style.width = (used / st.total_kb * 100).toFixed(1) + "%";
  } else { $("#storage-text").textContent = s.connected ? "—" : "offline"; $("#storage-bar").style.width = "0"; }
}
function onScreen(type, slot) {
  const c = state.status && state.status.current;
  if (!c) return false;
  if (c.kind === type) return c.slot === slot;
  return type === "jpg" && c.kind === "slideshow" && wallpaperSlot() === slot;
}
function wallpaperSlot() {
  const ss = state.config && state.config.slideshow;
  return state.config && state.config.mode === "slideshow" && ss.source === "jpg" ? ss.jpg_slot : null;
}
function describeCurrent(c) {
  if (c.kind === "hwmon") return "Hardware Monitor";
  if (c.kind === "slideshow") { const s = wallpaperSlot(); return "Wallpaper" + (s != null ? ` · JPG ${s}` : ""); }
  if (c.kind === "gif") return `Animation · GIF ${c.slot}`;
  if (c.kind === "jpg") return `Wallpaper · JPG ${c.slot}`;
  if (c.kind === "clock") return "Clock";
  return `mode ${c.kind}`;
}

// ---- dashboard ------------------------------------------------------------------------
function renderDashboard() {
  const s = state.status, st = s.storage, m = s.monitor || {};
  const tiles = [
    ["Connection", s.connected ? (s.demo ? "Demo" : "Online") : "Offline", s.path || s.error || ""],
    ["Firmware", s.firmware || "—", "AURJ2-S750"],
    ["On screen", s.current ? describeCurrent(s.current) : "—", ""],
    ["Brightness", s.display ? s.display.brightness + "%" : "—", s.display && s.display.standby ? "standby on" : "standby off"],
    ["Live feed", m.running ? "Running" : "Stopped", m.running ? `every ${m.interval} s` : "hardware-monitor sensors"],
    ["Storage", st && st.total_kb ? fmtKB(st.free_kb) + " free" : "—", st ? `${st.gif.used.length + st.jpg.used.length} files` : ""],
  ];
  $("#dash-tiles").innerHTML = tiles.map(([l, v, sm]) => `<div class="stat"><label>${l}</label><b>${esc(v)}</b><small>${esc(sm)}</small></div>`).join("");
}
function renderSensors() {
  const g = $("#sensor-grid");
  if (!state.sensors.length) { g.innerHTML = `<div class="empty-msg">No hwmon sensors found under /sys/class/hwmon.</div>`; return; }
  g.innerHTML = state.sensors.map((s) => `<div class="sensor ${s.kind}"><div class="id">${esc(s.id)}</div><div class="lbl">${esc(s.label)}</div><div class="v">${esc(s.value)}</div></div>`).join("");
}
function esc(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

// ---- display panel --------------------------------------------------------------------
function hydrateDisplay() {
  const f = state.form;
  $("#mode").value = f.mode;
  $("#duration").value = String([5, 10, 15, 30, 60].includes(f.slideshow.duration) ? f.slideshow.duration : 5);
  $$("#layouts .layout").forEach((b) => b.classList.toggle("active", +b.dataset.count === f.hwmon.count));
  $("#hw-bg").value = hex6(f.hwmon.bg); $("#hw-bg-txt").textContent = "#" + f.hwmon.bg;
  $("#hw-fg").value = hex6(f.hwmon.fg); $("#hw-fg-txt").textContent = "#" + f.hwmon.fg;
  $("#hw-live").checked = f.hwmon.live;
  $("#hw-interval").value = String(f.hwmon.interval);
  $$("#source input").forEach((r) => r.checked = r.value === f.slideshow.source);
  const bn = f.slideshow.banner;
  $("#bn-color").value = hex6(bn.color); $("#bn-color-txt").textContent = "#" + bn.color;
  $("#bn-alpha").value = bn.alpha; $("#bn-alpha-out").textContent = Math.round(bn.alpha / 255 * 100) + "%"; setPct($("#bn-alpha"));
  $$("#bn-align button").forEach((b) => b.classList.toggle("active", +b.dataset.v === bn.align));
  $("#bn-x").value = bn.x; $("#bn-x-out").textContent = bn.x + " px"; setPct($("#bn-x"));
  $$("#clock-fmt button").forEach((b) => b.classList.toggle("active", !!+b.dataset.v === f.slideshow.h24));
  renderLines(); renderBannerLines(); renderPickers(); showModeCards();
}
function showModeCards() {
  const f = state.form;
  $(".mode-hwmon").hidden = f.mode !== "hwmon";
  $(".mode-slideshow").hidden = f.mode !== "slideshow";
  $("#duration-field").hidden = f.mode !== "slideshow";
  $$(".source-pane").forEach((p) => p.hidden = p.dataset.source !== f.slideshow.source);
}
function sensorOptions(selected) {
  const groups = {};
  for (const s of state.sensors) (groups[s.hwmon] ||= []).push(s);
  let html = `<option value="">Custom text…</option>`;
  for (const [hw, list] of Object.entries(groups)) {
    html += `<optgroup label="${esc(hw)}">` + list.map((s) => `<option value="${esc(s.id)}" ${s.id === selected ? "selected" : ""}>${esc(s.label)} · ${esc(s.attr)} (${esc(s.value)})</option>`).join("") + `</optgroup>`;
  }
  if (selected && !state.sensors.some((s) => s.id === selected)) html += `<option value="${esc(selected)}" selected>${esc(selected)} (not found)</option>`;
  return html;
}
function renderLines() {
  const f = state.form, wrap = $("#hw-lines");
  wrap.innerHTML = "";
  for (let i = 0; i < f.hwmon.count; i++) {
    const l = f.hwmon.lines[i];
    const row = document.createElement("div"); row.className = "line";
    row.innerHTML = `<div class="num">${i + 1}</div>
      <input class="text" maxlength="18" placeholder="Label" value="${esc(l.label)}" data-k="label">
      <div class="select"><select data-k="sensor">${sensorOptions(l.sensor)}</select></div>
      <input class="text" maxlength="42" placeholder="Value" value="${esc(l.value)}" data-k="value" ${l.sensor ? "hidden" : ""}>
      <div class="val" ${l.sensor ? "" : "hidden"}>${esc(sensorValue(l.sensor))}</div>`;
    row.addEventListener("input", (e) => {
      const k = e.target.dataset.k; if (!k) return;
      l[k] = e.target.value;
      if (k === "sensor") {
        const sen = state.sensors.find((s) => s.id === l.sensor);
        if (sen && !l.label) { l.label = sen.label.slice(0, 18); $("[data-k=label]", row).value = l.label; }
        $("[data-k=value]", row).hidden = !!l.sensor; $(".val", row).hidden = !l.sensor;
      }
      onFormChange();
    });
    wrap.appendChild(row);
  }
}
function refreshSensorOptions() {
  $$("#hw-lines .line").forEach((row, i) => {
    const l = state.form.hwmon.lines[i]; if (!l) return;
    const sel = $("select", row);
    if (document.activeElement !== sel) sel.innerHTML = sensorOptions(l.sensor);
    $(".val", row).textContent = l.sensor ? sensorValue(l.sensor) : "";
  });
}
function sensorValue(id) { const s = state.sensors.find((x) => x.id === id); return s ? s.value : (id ? "n/a" : ""); }
function renderBannerLines() {
  const bn = state.form.slideshow.banner, wrap = $("#banner-lines");
  wrap.innerHTML = "";
  bn.lines.forEach((t, i) => {
    const inp = document.createElement("input"); inp.className = "text"; inp.maxLength = 48; inp.placeholder = `Line ${i + 1}`; inp.value = t;
    inp.addEventListener("input", () => { bn.lines[i] = inp.value; onFormChange(); });
    wrap.appendChild(inp);
  });
}
function tileHTML(type, item, opts = {}) {
  const key = `${type}-${item.slot}`;
  const playing = item.used && onScreen(type, item.slot);
  const cls = ["tile", opts.library ? "library" : "", opts.selected ? "selected" : "", playing ? "playing" : "", item.used ? (item.cached ? "" : "unknown") : "empty", opts.add ? "add" : ""].join(" ");
  let inner;
  if (opts.add) inner = `<span class="plus">+</span><span class="add-txt">Add ${type === "gif" ? "animation" : "wallpaper"}</span>`;
  else if (!item.used) inner = `<span class="plus">+</span>`;
  else if (item.cached) inner = `<img src="/api/media/${type}/${item.slot}?v=${item.bytes}" alt="">`;
  else inner = `<span>no local copy</span>`;
  const name = item.used && item.cached ? `<div class="name">${esc(item.name || "")}${item.bytes ? ` <span class="hint">${fmtBytes(item.bytes)}</span>` : ""}</div>` : "";
  const x = item.used && opts.deletable ? `<button class="x" data-act="delete" title="Delete">✕</button>` : "";
  const use = item.used && opts.library ? `<div class="use"><button class="btn primary small" data-act="show"><span>Show now</span></button>${item.cached ? "" : `<button class="btn small" data-act="thumb">Set thumbnail</button>`}</div>` : "";
  return `<div class="${cls}" role="button" tabindex="0" data-type="${type}" data-slot="${item.slot}" data-key="${key}" data-used="${item.used ? 1 : 0}"><span class="slot">${type.toUpperCase()} ${item.slot}</span>${inner}${name}${x}${use}</div>`;
}
function slotItems(type) {
  const st = state.status && state.status.storage;
  if (st && st[type] && st[type].items) return st[type].items;
  return Array.from({ length: SLOTS }, (_, i) => ({ slot: i, used: false, cached: false }));
}
// the slideshow pickers show what is stored plus one "+" tile for the next free slot;
// the Media page shows the whole slot map
function pickerHTML(type, selected) {
  const items = slotItems(type), free = items.find((it) => !it.used);
  const html = items.filter((it) => it.used).map((it) => tileHTML(type, it, { selected: selected === it.slot }));
  if (free) html.push(tileHTML(type, free, { add: true }));
  return html.join("");
}
function renderPickers() {
  if (!state.form) return;
  const f = state.form;
  $("#gif-picker").innerHTML = pickerHTML("gif", f.slideshow.gif_slot);
  $("#jpg-picker").innerHTML = pickerHTML("jpg", f.slideshow.jpg_slot);
  const anim = state.settings ? state.settings.anim_slot : null;
  $("#standby-picker").innerHTML = slotItems("gif").filter((it) => it.used).map((it) => tileHTML("gif", it, { selected: anim === it.slot })).join("")
    || `<div class="empty-msg">No animations stored yet. Upload a GIF in the Media library.</div>`;
}
function renderMedia() {
  $("#gif-library").innerHTML = slotItems("gif").map((it) => tileHTML("gif", it, { library: true, deletable: true })).join("");
  $("#jpg-library").innerHTML = slotItems("jpg").map((it) => tileHTML("jpg", it, { library: true, deletable: true })).join("");
}
function onFormChange() { renderApplyBar(); drawPreview(); }
function renderApplyBar() {
  const bar = $("#applybar"), st = $("#apply-status");
  bar.hidden = state.panel !== "display";
  if (!state.form) return;
  const dirty = isDirty();
  st.classList.toggle("dirty", dirty);
  const m = state.status && state.status.monitor;
  st.textContent = dirty ? "Changes not applied to the device yet." : (m && m.running ? `Applied · live feed running${m.error ? " · " + m.error : ""}` : "Applied.");
  $("#apply-revert").disabled = !dirty;
}

// ---- apply --------------------------------------------------------------------------
async function apply() {
  const f = state.form, btn = $("#apply");
  btn.disabled = true;
  try {
    if (f.mode === "hwmon") {
      const lines = f.hwmon.lines.slice(0, f.hwmon.count).map((l) => l.sensor ? { label: l.label, sensor: l.sensor } : { label: l.label, value: l.value });
      if (lines.some((l) => !l.label && !l.sensor && !l.value)) throw new Error("every line needs a label and a sensor or text");
      const r = await api("POST", "/api/hwmon", { lines, bg: f.hwmon.bg, fg: f.hwmon.fg, live: f.hwmon.live, interval: f.hwmon.interval });
      toast(r.monitor && r.monitor.running ? "Hardware monitor applied, live feed running." : "Hardware monitor applied.");
    } else {
      const ss = f.slideshow, body = { source: ss.source, duration: ss.duration, h24: ss.h24 };
      if (ss.source === "gif") { if (ss.gif_slot == null) throw new Error("select an animation"); body.slot = ss.gif_slot; }
      if (ss.source === "jpg") {
        if (ss.jpg_slot == null) throw new Error("select a wallpaper");
        body.slot = ss.jpg_slot;
        body.banner = { lines: ss.banner.lines, color: ss.banner.color + ss.banner.alpha.toString(16).padStart(2, "0").toUpperCase(), align: ss.banner.align, x: ss.banner.x, font: ss.banner.font };
      }
      await api("POST", "/api/show", body);
      toast(`${KIND_LABEL[ss.source]} applied.`);
    }
    await loadStatus();
    state.form = formFromConfig(state.config); hydrateDisplay(); renderApplyBar();
  } catch (e) { toast(e.message, "err", 6000); }
  btn.disabled = false;
}

// ---- preview ------------------------------------------------------------------------
function imageFor(type, slot) {
  const items = slotItems(type), it = items[slot];
  if (!it || !it.cached) return null;
  const key = `${type}-${slot}-${it.bytes}`;
  if (!state.images[key]) {
    const im = new Image(); im.src = `/api/media/${type}/${slot}?v=${it.bytes}`; im.onload = drawPreview; state.images[key] = im;
  }
  return state.images[key].complete && state.images[key].naturalWidth ? state.images[key] : null;
}
function drawPreview() {
  const c = $("#preview"), ctx = c.getContext("2d"), f = state.form;
  ctx.clearRect(0, 0, 320, 240);
  clearInterval(state.clockTimer); state.clockTimer = null;
  if (!f) { noSignal(ctx, "CONNECTING"); return; }
  let caption = "Preview";
  if (f.mode === "hwmon") {
    caption = "Preview · Hardware Monitor";
    const h = f.hwmon, n = h.count, bg = "#" + h.bg, fg = "#" + h.fg;
    ctx.fillStyle = bg; ctx.fillRect(0, 0, 320, 240);
    const rowH = 240 / n;
    for (let i = 0; i < n; i++) {
      const l = h.lines[i]; const y = rowH * i;
      const val = glyphs(l.sensor ? sensorValue(l.sensor) : l.value || "");
      ctx.fillStyle = fg; ctx.globalAlpha = .65;
      ctx.font = `${n === 1 ? 22 : 15}px "Segoe UI", Roboto, Arial, sans-serif`; ctx.textBaseline = "alphabetic"; ctx.textAlign = "left";
      ctx.fillText((l.label || `Line ${i + 1}`).toUpperCase(), 18, y + (n === 1 ? 70 : rowH * .42));
      ctx.globalAlpha = 1;
      ctx.font = `600 ${n === 1 ? 64 : n === 2 ? 44 : 34}px "Segoe UI", Roboto, Arial, sans-serif`; ctx.textAlign = "right";
      ctx.fillText(val, 302, y + (n === 1 ? 160 : rowH * .86));
      if (i < n - 1) { ctx.globalAlpha = .18; ctx.fillRect(18, y + rowH - 1, 284, 1); ctx.globalAlpha = 1; }
    }
  } else if (f.slideshow.source === "clock") {
    caption = "Preview · Clock";
    const draw = () => {
      const d = new Date(); let hh = d.getHours(); const ampm = hh >= 12 ? "PM" : "AM";
      if (!f.slideshow.h24) hh = hh % 12 || 12;
      ctx.fillStyle = "#000"; ctx.fillRect(0, 0, 320, 240);
      ctx.fillStyle = "#fff"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.font = `600 62px "Segoe UI", Roboto, Arial, sans-serif`;
      ctx.fillText(`${String(hh).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`, 160, 112);
      ctx.font = `24px "Segoe UI", Roboto, Arial, sans-serif`; ctx.fillStyle = "#e11d3a";
      ctx.fillText(String(d.getSeconds()).padStart(2, "0") + (f.slideshow.h24 ? "" : "  " + ampm), 160, 168);
    };
    draw(); state.clockTimer = setInterval(draw, 1000);
  } else {
    const type = f.slideshow.source, slot = type === "gif" ? f.slideshow.gif_slot : f.slideshow.jpg_slot;
    caption = `Preview · ${KIND_LABEL[type]}${slot != null ? " " + slot : ""}`;
    if (slot == null) { noSignal(ctx, `SELECT ${type === "gif" ? "AN ANIMATION" : "A WALLPAPER"}`); }
    else {
      const im = imageFor(type, slot);
      if (im) ctx.drawImage(im, 0, 0, 320, 240);
      else { ctx.fillStyle = "#101014"; ctx.fillRect(0, 0, 320, 240); noSignalText(ctx, slotItems(type)[slot] && slotItems(type)[slot].used ? `${type.toUpperCase()} ${slot} · NO LOCAL COPY` : `${type.toUpperCase()} ${slot} · EMPTY`); }
      if (type === "jpg") {
        const bn = f.slideshow.banner; const a = bn.alpha / 255;
        ctx.font = `17px "Segoe UI", Roboto, Arial, sans-serif`; ctx.textBaseline = "top";
        ctx.fillStyle = "#" + bn.color; ctx.globalAlpha = a; ctx.textAlign = bn.align ? "right" : "left";
        bn.lines.forEach((t, i) => t && ctx.fillText(t, bn.x, 23 + 40 * i - 4));
        ctx.globalAlpha = 1;
      }
    }
  }
  $("#preview-label").textContent = caption;
}
function noSignal(ctx, text) { ctx.fillStyle = "#000"; ctx.fillRect(0, 0, 320, 240); noSignalText(ctx, text); }
function noSignalText(ctx, text) {
  ctx.fillStyle = "#555"; ctx.font = `12px ui-monospace, Consolas, monospace`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillText(text, 160, 120);
  ctx.strokeStyle = "#e11d3a"; ctx.lineWidth = 1; ctx.strokeRect(8.5, 8.5, 303, 223);
}

// ---- media: upload / delete / show ---------------------------------------------------
let pending = null; // {type, slot, file, url, w, h, scale, base, x, y}
function pickFile(type, slot, thumb = false) {
  const inp = $("#file-input");
  inp.accept = type === "gif" ? "image/gif" : "image/*";
  inp.value = ""; inp.dataset.type = type; inp.dataset.slot = slot; inp.dataset.thumb = thumb ? "1" : "";
  inp.click();
}
$("#file-input").addEventListener("change", (e) => {
  const file = e.target.files[0]; if (!file) return;
  openCrop(e.target.dataset.type, +e.target.dataset.slot, file, e.target.dataset.thumb === "1");
});
function openCrop(type, slot, file, thumb = false) {
  const url = URL.createObjectURL(file), img = $("#crop-img");
  pending = { type, slot, file, url, raw: false, thumb };
  img.onload = () => {
    const stage = $("#crop-stage");
    const W = stage.clientWidth, H = Math.round(W * 3 / 4); stage.style.height = H + "px";
    pending.w = img.naturalWidth; pending.h = img.naturalHeight; pending.W = W; pending.H = H;
    pending.base = Math.max(W / pending.w, H / pending.h); pending.zoom = 1;
    pending.x = (W - pending.w * pending.base) / 2; pending.y = (H - pending.h * pending.base) / 2;
    $("#crop-zoom").value = 1; setPct($("#crop-zoom"));
    layoutCrop();
  };
  img.src = url;
  $("#crop-title").textContent = `${thumb ? "Thumbnail" : "Crop"} · ${file.name}`;
  $("#crop-target").textContent = `${type === "gif" ? "Animation" : "Wallpaper"} slot ${slot} · ${fmtBytes(file.size)}${thumb ? " · local copy only, the cooler keeps what it has" : ""}`;
  $("#crop-save span").textContent = thumb ? "Save thumbnail" : "Save";
  $("#crop-modal").hidden = false;
}
function layoutCrop() {
  const p = pending, img = $("#crop-img"), s = p.base * p.zoom;
  const minX = p.W - p.w * s, minY = p.H - p.h * s;
  p.x = Math.min(0, Math.max(minX, p.x)); p.y = Math.min(0, Math.max(minY, p.y));
  img.style.transform = `translate(${p.x}px, ${p.y}px) scale(${s})`;
  const crop = cropRect();
  $("#crop-info").textContent = `${p.w}×${p.h} → crop ${Math.round(crop[2])}×${Math.round(crop[3])} at ${Math.round(crop[0])},${Math.round(crop[1])} → 320×240`;
}
function cropRect() { const p = pending, s = p.base * p.zoom; return [-p.x / s, -p.y / s, p.W / s, p.H / s]; }
(() => {
  const stage = $("#crop-stage"); let drag = null;
  stage.addEventListener("pointerdown", (e) => { if (!pending) return; drag = { x: e.clientX - pending.x, y: e.clientY - pending.y }; stage.setPointerCapture(e.pointerId); });
  stage.addEventListener("pointermove", (e) => { if (!drag) return; pending.x = e.clientX - drag.x; pending.y = e.clientY - drag.y; layoutCrop(); });
  stage.addEventListener("pointerup", () => drag = null);
  stage.addEventListener("wheel", (e) => { if (!pending) return; e.preventDefault(); const z = $("#crop-zoom"); z.value = Math.min(4, Math.max(1, +z.value * (e.deltaY < 0 ? 1.08 : 1 / 1.08))); z.dispatchEvent(new Event("input")); }, { passive: false });
  $("#crop-zoom").addEventListener("input", (e) => {
    if (!pending) return; const p = pending, nz = +e.target.value, cx = p.W / 2, cy = p.H / 2;
    const s0 = p.base * p.zoom, s1 = p.base * nz;      // zoom around the frame center
    p.x = cx - (cx - p.x) * s1 / s0; p.y = cy - (cy - p.y) * s1 / s0; p.zoom = nz; setPct(e.target); layoutCrop();
  });
})();
$("#crop-save").addEventListener("click", () => doUpload());
function closeModals() { $$(".modal").forEach((m) => m.hidden = true); if (pending && pending.url) URL.revokeObjectURL(pending.url); pending = null; }
$$("[data-close]").forEach((b) => b.addEventListener("click", closeModals));
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModals(); });

function doUpload() {
  const p = pending; if (!p) return;
  const crop = cropRect().map((v) => v.toFixed(2)).join(",");
  const q = new URLSearchParams({ type: p.type, slot: p.slot, name: p.file.name, crop });
  const xhr = new XMLHttpRequest();
  $("#crop-modal").hidden = true;
  busy(`Preparing ${p.file.name}…`, 0);
  xhr.upload.onprogress = (e) => { if (e.lengthComputable) busy(`Sending to host… ${Math.round(e.loaded / e.total * 100)}%`, e.loaded / e.total * 50); };
  xhr.upload.onload = () => busy(p.thumb ? "Converting…" : "Converting and writing to the cooler…", 75);
  xhr.onload = async () => {
    let d = {}; try { d = JSON.parse(xhr.responseText); } catch (e) { /* ignore */ }
    if (xhr.status >= 200 && xhr.status < 300) {
      toast(p.thumb ? `Thumbnail set for ${p.type.toUpperCase()} slot ${p.slot}.` : `Stored ${fmtBytes(d.bytes)} in ${p.type.toUpperCase()} slot ${p.slot} (${d.seconds}s).`);
      if (p.type === "gif" && state.form.slideshow.gif_slot == null) state.form.slideshow.gif_slot = p.slot;
      if (p.type === "jpg" && state.form.slideshow.jpg_slot == null) state.form.slideshow.jpg_slot = p.slot;
    } else toast(d.error || `upload failed (${xhr.status})`, "err", 7000);
    closeModals(); await loadStatus();
  };
  xhr.onerror = () => { toast("upload failed: network error", "err"); closeModals(); };
  xhr.open("POST", (p.thumb ? "/api/thumbnail?" : "/api/upload?") + q.toString());
  xhr.send(p.file);
}
function busy(text, pct) { $("#busy-modal").hidden = false; $("#busy-text").textContent = text; $("#busy-bar").style.width = pct + "%"; }
function confirmDialog(title, text) {
  return new Promise((res) => {
    $("#confirm-title").textContent = title; $("#confirm-text").textContent = text; $("#confirm-modal").hidden = false;
    const ok = $("#confirm-ok"), done = (v) => { ok.onclick = null; $("#confirm-modal").hidden = true; res(v); };
    ok.onclick = () => done(true);
    $$("#confirm-modal [data-close]").forEach((b) => b.onclick = () => done(false));
  });
}
async function deleteMedia(type, slot) {
  const it = slotItems(type)[slot];
  if (!(await confirmDialog("Delete file", `Remove ${it && it.name ? it.name : type.toUpperCase() + " slot " + slot} from the cooler's storage? The file is deleted from the device.`))) return;
  try {
    busy(`Deleting ${type.toUpperCase()} slot ${slot}…`, 50);
    await api("DELETE", `/api/media/${type}/${slot}`);
    toast(`Deleted ${type.toUpperCase()} slot ${slot}.`);
    if (state.form.slideshow[type + "_slot"] === slot) state.form.slideshow[type + "_slot"] = null;
  } catch (e) { toast(e.message, "err", 6000); }
  closeModals(); await loadStatus();
}
async function showNow(type, slot) {
  const ss = state.form.slideshow;
  state.form.mode = "slideshow"; ss.source = type; ss[type + "_slot"] = slot;
  hydrateDisplay(); await apply();
}
function onTileClick(e, opts) {
  if (e.type === "keydown" && e.key !== "Enter" && e.key !== " ") return;
  const act = e.target.closest("[data-act]"), tile = e.target.closest(".tile");
  if (!tile) return;
  const type = tile.dataset.type, slot = +tile.dataset.slot, used = tile.dataset.used === "1";
  if (act && act.dataset.act === "delete") { e.stopPropagation(); return deleteMedia(type, slot); }
  if (act && act.dataset.act === "show") { e.stopPropagation(); return showNow(type, slot); }
  if (act && act.dataset.act === "thumb") { e.stopPropagation(); return pickFile(type, slot, true); }
  if (!used) return pickFile(type, slot);
  opts.select(type, slot);
}
const TILE_HANDLERS = {
  "#gif-picker": { select: (t, s) => { state.form.slideshow.gif_slot = s; renderPickers(); onFormChange(); } },
  "#jpg-picker": { select: (t, s) => { state.form.slideshow.jpg_slot = s; renderPickers(); onFormChange(); } },
  "#gif-library": { select: () => {} },
  "#jpg-library": { select: () => {} },
  "#standby-picker": { select: (t, s) => { state.settings.anim_slot = s; state.settingsDirty = true; renderPickers(); } },
};
for (const [sel, opts] of Object.entries(TILE_HANDLERS)) for (const ev of ["click", "keydown"]) $(sel).addEventListener(ev, (e) => onTileClick(e, opts));

// ---- settings panel ------------------------------------------------------------------
function renderSettings() {
  const d = state.status && state.status.display;
  if (!d) return;
  if (!state.settings || !state.settingsDirty) {
    state.settings = { brightness: Math.max(10, Math.round(d.brightness / 10) * 10 || 10), standby: d.standby, anim_slot: d.anim_slot };
    $("#brightness").value = state.settings.brightness; $("#brightness-out").textContent = state.settings.brightness + "%"; setPct($("#brightness"));
    $("#standby").checked = state.settings.standby;
  }
}
$("#brightness").addEventListener("input", (e) => { state.settings.brightness = +e.target.value; $("#brightness-out").textContent = e.target.value + "%"; setPct(e.target); state.settingsDirty = true; });
$("#standby").addEventListener("change", (e) => { state.settings.standby = e.target.checked; state.settingsDirty = true; });
$("#settings-reset").addEventListener("click", () => { state.settingsDirty = false; renderSettings(); renderPickers(); });
$("#settings-save").addEventListener("click", async () => {
  const b = $("#settings-save"); b.disabled = true;
  try {
    const r = await api("POST", "/api/display", state.settings);
    toast(`Saved: brightness ${r.display.brightness}%, standby ${r.display.standby ? "on" : "off"}.`);
    state.settingsDirty = false; await loadStatus();
  } catch (e) { toast(e.message, "err", 6000); }
  b.disabled = false;
});

// ---- advanced panel ------------------------------------------------------------------
function renderAdvanced() {
  const s = state.status, rows = [
    ["State", s.connected ? "connected" : "not connected"], ["Error", s.error || "—"], ["Node", s.path || "—"], ["Firmware", s.firmware || "—"],
    ["Display status", s.display ? s.display.raw : "—"], ["Storage table", s.storage && s.storage.raw ? s.storage.raw : "—"],
    ["Brightness bytes", s.display ? `byte 7 = ${s.display.brightness}, byte 12 = ${s.display.brightness_ac}` : "—"],
    ["Standby animation", s.display ? `type ${s.display.anim_type} slot ${s.display.anim_slot}` : "—"],
    ["Pillow", s.pillow ? "available (images are cropped and resized on upload)" : "missing: only raw 320×240 files can be sent"],
  ];
  $("#device-kv").innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("");
  const m = s.monitor || {};
  $("#monitor-state").textContent = m.running ? `running every ${m.interval} s · ${(m.lines || []).map(([l, v]) => `${l} ${glyphs(v)}`).join(" | ")}${m.error ? "\nerror: " + m.error : ""}` : "stopped";
  $("#monitor-stop").disabled = !m.running;
}
$("#monitor-stop").addEventListener("click", async () => { try { await api("POST", "/api/monitor/stop"); toast("Live feed stopped."); await loadStatus(); } catch (e) { toast(e.message, "err"); } });
$("#raw-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const hex = $("#raw-hex").value.trim(); if (!hex) return;
  const log = $("#raw-log");
  try {
    const r = await api("POST", "/api/raw", { hex });
    log.innerHTML += `<span class="in">W ${esc(r.sent)}</span>\nR ${esc(r.reply)}\n`;
  } catch (err) { log.innerHTML += `<span class="err">${esc(err.message)}</span>\n`; }
  log.scrollTop = log.scrollHeight;
});

// ---- wiring ---------------------------------------------------------------------------
$$(".rail-item").forEach((b) => b.addEventListener("click", () => {
  state.panel = b.dataset.panel;
  $$(".rail-item").forEach((x) => x.classList.toggle("active", x === b));
  $$(".panel").forEach((p) => p.classList.toggle("active", p.dataset.panel === state.panel));
  renderApplyBar();
}));
$("#refresh").addEventListener("click", () => { loadStatus(true); loadSensors(); });
$("#mode").addEventListener("change", (e) => { state.form.mode = e.target.value; showModeCards(); onFormChange(); });
$("#duration").addEventListener("change", (e) => { state.form.slideshow.duration = +e.target.value; onFormChange(); });
$("#layouts").addEventListener("click", (e) => {
  const b = e.target.closest(".layout"); if (!b) return;
  state.form.hwmon.count = +b.dataset.count;
  $$("#layouts .layout").forEach((x) => x.classList.toggle("active", x === b));
  renderLines(); onFormChange();
});
$("#hw-bg").addEventListener("input", (e) => { state.form.hwmon.bg = fromHex6(e.target.value); $("#hw-bg-txt").textContent = "#" + state.form.hwmon.bg; onFormChange(); });
$("#hw-fg").addEventListener("input", (e) => { state.form.hwmon.fg = fromHex6(e.target.value); $("#hw-fg-txt").textContent = "#" + state.form.hwmon.fg; onFormChange(); });
$("#hw-live").addEventListener("change", (e) => { state.form.hwmon.live = e.target.checked; onFormChange(); });
$("#hw-interval").addEventListener("change", (e) => { state.form.hwmon.interval = +e.target.value; onFormChange(); });
$("#source").addEventListener("change", (e) => { state.form.slideshow.source = e.target.value; showModeCards(); onFormChange(); });
$("#bn-color").addEventListener("input", (e) => { state.form.slideshow.banner.color = fromHex6(e.target.value); $("#bn-color-txt").textContent = "#" + state.form.slideshow.banner.color; onFormChange(); });
$("#bn-alpha").addEventListener("input", (e) => { state.form.slideshow.banner.alpha = +e.target.value; $("#bn-alpha-out").textContent = Math.round(e.target.value / 255 * 100) + "%"; setPct(e.target); onFormChange(); });
$("#bn-x").addEventListener("input", (e) => { state.form.slideshow.banner.x = +e.target.value; $("#bn-x-out").textContent = e.target.value + " px"; setPct(e.target); onFormChange(); });
$("#bn-align").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; state.form.slideshow.banner.align = +b.dataset.v; $$("#bn-align button").forEach((x) => x.classList.toggle("active", x === b)); onFormChange(); });
$("#clock-fmt").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; state.form.slideshow.h24 = !!+b.dataset.v; $$("#clock-fmt button").forEach((x) => x.classList.toggle("active", x === b)); onFormChange(); });
$("#apply").addEventListener("click", apply);
$("#apply-revert").addEventListener("click", () => { state.form = formFromConfig(state.config); hydrateDisplay(); onFormChange(); });

drawPreview();
loadStatus().then(loadSensors);
setInterval(loadStatus, 5000);
setInterval(loadSensors, 2000);
})();
