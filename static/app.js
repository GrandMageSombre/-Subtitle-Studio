/* ============================================================
 * 字幕工坊 Subtitle Studio · 前端逻辑
 * 模块：状态 / 历史 / 渲染 / 字幕编辑 / 时间轴 / 播放器 / 弹窗
 * ============================================================ */
"use strict";
(() => {

const $ = (id) => document.getElementById(id);
const MONO = 'ui-monospace, Consolas, monospace';

const video = $("video");
const cueListEl = $("cueList");
const canvas = $("timeline");
const ctx = canvas.getContext("2d");
const playheadEl = $("playhead");
const thumbEl = $("tlThumb");
const scrollbarEl = $("tlScrollbar");
const overlayEl = $("subOverlay");
const toastEl = $("toast");
const globalProgress = $("globalProgress");
const globalProgressBar = $("globalProgressBar");
const globalProgressText = $("globalProgressText");
const LS_LAST_JOB = "subtitle-studio:lastJob";

// 命名常量（替代散落的魔法数字）
const POLL_INTERVAL_MS = 900;
const SAVE_DEBOUNCE_MS = 1200;
const HISTORY_LIMIT = 30;
const TOAST_MS = 3600;
const POLL_MAX_RETRY = 12;

const state = {
  job: null,            // {id, kind, filename, has_media}
  cues: [],
  selected: -1,
  activeIdx: -1,
  playCueEnd: null,
  localVideoUrl: null,
  history: [],
  future: [],
  saveTimer: null,
  saving: false,
  savePending: false,
  pollTimer: null,
  recFile: null,
  editSnapshotTaken: false,
};

let overlapFlags = [];

/* ---------------- 工具 ---------------- */

const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const round3 = (v) => Math.round(v * 1000) / 1000;
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
  .replace(/>/g, "&gt;").replace(/"/g, "&quot;");

function fmtTime(s) {
  if (!isFinite(s) || s < 0) s = 0;
  const ms = Math.round(s * 1000);
  const h = Math.floor(ms / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  const sec = Math.floor((ms % 60000) / 1000);
  const mss = ms % 1000;
  const pad = (v, n = 2) => String(v).padStart(n, "0");
  return `${pad(h)}:${pad(m)}:${pad(sec)}.${pad(mss, 3)}`;
}

function fmtTimeShort(s) {
  s = Math.max(0, s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = Math.floor(s % 60);
  const pad = (v) => String(v).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(ss)}` : `${pad(m)}:${pad(ss)}`;
}

function fmtDur(s) {
  const m = Math.floor(s / 60), ss = Math.round(s % 60);
  return `${m}分${String(ss).padStart(2, "0")}秒`;
}

function parseTime(str) {
  const m = String(str).trim().match(/^(?:(\d{1,3}):)?(\d{1,2}):(\d{1,2})(?:[.,](\d{1,3}))?$/);
  if (!m) return null;
  const h = m[1] ? parseInt(m[1], 10) : 0;
  const mm = parseInt(m[2], 10), ss = parseInt(m[3], 10);
  const ms = m[4] ? parseInt(m[4].padEnd(3, "0"), 10) : 0;
  return h * 3600 + mm * 60 + ss + ms / 1000;
}

let toastTimer = null;
function toast(msg, type = "") {
  toastEl.textContent = msg;
  toastEl.className = type || "";
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.add("hidden"), TOAST_MS);
}

async function api(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) {
    let detail = `HTTP ${r.status}`;
    try {
      const j = await r.json();
      if (j.detail) detail = `${r.status}: ${j.detail}`;
    } catch (_) { /* ignore */ }
    throw new Error(detail);
  }
  return r.json();
}

/* ---------------- 历史（撤销/重做） ---------------- */

function snapshot() {
  state.history.push(JSON.stringify(state.cues));
  if (state.history.length > HISTORY_LIMIT) state.history.shift();
  state.future.length = 0;
}

function undo() {
  if (!state.history.length) return;
  state.future.push(JSON.stringify(state.cues));
  state.cues = JSON.parse(state.history.pop());
  afterStructuralChange();
}

function redo() {
  if (!state.future.length) return;
  state.history.push(JSON.stringify(state.cues));
  state.cues = JSON.parse(state.future.pop());
  afterStructuralChange();
}

function afterStructuralChange() {
  state.selected = clamp(state.selected, -1, state.cues.length - 1);
  computeOverlaps();
  renderList();
  drawTimeline();
  updateStats();
  scheduleSave();
}

/* ---------------- 渲染：字幕列表 ---------------- */

function rowHtml(c, i) {
  const lines = String(c.text).split("\n").length;
  const h = Math.max(30, lines * 20 + 8);
  return `<div class="cue-row" data-idx="${i}">
    <span class="cue-idx">${i + 1}</span>
    <input class="cue-time" data-field="start" value="${fmtTime(c.start)}" spellcheck="false" placeholder="时:分:秒.毫秒" title="开始时间，格式 时:分:秒.毫秒">
    <input class="cue-time" data-field="end" value="${fmtTime(c.end)}" spellcheck="false" placeholder="时:分:秒.毫秒" title="结束时间，格式 时:分:秒.毫秒">
    <span class="cue-dur">${(c.end - c.start).toFixed(2)}s</span>
    <textarea class="cue-text" rows="1" spellcheck="false" style="height:${h}px">${esc(c.text)}</textarea>
    <button class="cue-play" title="播放此段">▶</button>
  </div>`;
}

function renderList() {
  cueListEl.innerHTML = state.cues.map(rowHtml).join("");
  $("listEmpty").classList.toggle("hidden", state.cues.length > 0);
  applyRowStates();
}

function computeOverlaps() {
  overlapFlags = state.cues.map(() => false);
  for (let i = 1; i < state.cues.length; i++) {
    if (state.cues[i].start < state.cues[i - 1].end - 0.001) overlapFlags[i] = true;
  }
}

function applyRowStates() {
  cueListEl.querySelectorAll(".cue-row").forEach((row) => {
    const i = +row.dataset.idx;
    row.classList.toggle("selected", i === state.selected);
    row.classList.toggle("playing", i === state.activeIdx);
    row.classList.toggle("overlap", !!overlapFlags[i]);
  });
}

function updateRowInputs(i) {
  const row = cueListEl.querySelector(`.cue-row[data-idx="${i}"]`);
  if (!row) return;
  const c = state.cues[i];
  row.querySelector('[data-field="start"]').value = fmtTime(c.start);
  row.querySelector('[data-field="end"]').value = fmtTime(c.end);
  row.querySelector(".cue-dur").textContent = (c.end - c.start).toFixed(2) + "s";
}

function updateStats() {
  const n = state.cues.length;
  const overlaps = overlapFlags.filter(Boolean).length;
  const span = n ? state.cues[n - 1].end - state.cues[0].start : 0;
  $("stats").innerHTML =
    `<b>${n}</b> 条 · 跨度 <b>${fmtDur(span)}</b>` +
    (overlaps ? ` · <b style="color:var(--danger)">${overlaps}</b> 处重叠` : "");
}

/* ---------------- 渲染：时间轴 ---------------- */

const tl = { zoom: 1, viewStart: 0, duration: 10, view: 10, pxPerSec: 10, w: 0, h: 0 };

function computeDuration() {
  let d = video.src && isFinite(video.duration) ? video.duration : 0;
  for (const c of state.cues) d = Math.max(d, c.end + 0.3);
  return Math.max(d, 1);
}

function resizeCanvas() {
  const rect = $("timelineWrap").getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  tl.w = rect.width;
  tl.h = rect.height;
  canvas.width = Math.max(1, Math.round(rect.width * dpr));
  canvas.height = Math.max(1, Math.round(rect.height * dpr));
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function updateTlGeom() {
  tl.duration = computeDuration();
  tl.view = tl.duration / tl.zoom;
  tl.viewStart = clamp(tl.viewStart, 0, Math.max(0, tl.duration - tl.view));
  tl.pxPerSec = tl.w / tl.view;
}

const tlX = (t) => (t - tl.viewStart) * tl.pxPerSec;
const tlT = (x) => tl.viewStart + x / tl.pxPerSec;

function roundRect(c, x, y, w, h, r) {
  r = Math.min(r, w / 2, h / 2);
  c.beginPath();
  c.moveTo(x + r, y);
  c.arcTo(x + w, y, x + w, y + h, r);
  c.arcTo(x + w, y + h, x, y + h, r);
  c.arcTo(x, y + h, x, y, r);
  c.arcTo(x, y, x + w, y, r);
  c.closePath();
}

function drawTimeline() {
  updateTlGeom();
  const { w, h } = tl;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#eef1f7";
  ctx.fillRect(0, 0, w, h);

  // 标尺
  const steps = [0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600];
  const step = steps.find((s) => s * tl.pxPerSec >= 64) ?? 3600;
  ctx.font = `10px ${MONO}`;
  ctx.fillStyle = "#9aa5ba";
  ctx.strokeStyle = "#d6dce8";
  ctx.lineWidth = 1;
  const t0 = Math.floor(tl.viewStart / step) * step;
  for (let t = t0; t <= tl.viewStart + tl.view + step; t += step) {
    const x = Math.round(tlX(t)) + 0.5;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, 8);
    ctx.stroke();
    ctx.fillText(fmtTimeShort(t), x + 3, 17);
  }

  // 分隔线
  ctx.strokeStyle = "#dde3ee";
  ctx.beginPath();
  ctx.moveTo(0, 22.5);
  ctx.lineTo(w, 22.5);
  ctx.stroke();

  // 字幕色块
  const top = 26, bh = h - top - 6;
  state.cues.forEach((c, i) => {
    const x1 = tlX(c.start), x2 = tlX(c.end);
    if (x2 < -20 || x1 > w + 20) return;
    const bw = Math.max(2, x2 - x1);
    let fill = "#8fb0f9";
    if (i === state.selected) fill = "#f5a623";
    else if (i === state.activeIdx) fill = "#37b26a";
    if (overlapFlags[i]) fill = i === state.selected ? "#e8873a" : "#f0a9a4";
    ctx.fillStyle = fill;
    roundRect(ctx, x1, top, bw, bh, 3);
    ctx.fill();
    if (bw > 30) {
      ctx.save();
      ctx.beginPath();
      ctx.rect(x1 + 2, top, bw - 4, bh);
      ctx.clip();
      ctx.fillStyle = "rgba(255,255,255,.92)";
      ctx.font = "10px system-ui";
      ctx.fillText(c.text.split("\n")[0], x1 + 5, top + bh / 2 + 3);
      ctx.restore();
    }
  });

  updateScrollbar();
  updatePlayhead();
}

function updateScrollbar() {
  const trackW = scrollbarEl.clientWidth || 1;
  const frac = clamp(tl.view / tl.duration, 0.02, 1);
  const thumbW = Math.max(28, trackW * frac);
  const maxLeft = trackW - thumbW;
  const left = tl.duration > 0 ? (tl.viewStart / tl.duration) * trackW : 0;
  thumbEl.style.width = thumbW + "px";
  thumbEl.style.left = clamp(left, 0, maxLeft) + "px";
}

function updatePlayhead() {
  if (!video.src || !isFinite(video.duration)) {
    playheadEl.style.display = "none";
    return;
  }
  const x = tlX(video.currentTime);
  if (x < -2 || x > tl.w + 2) {
    playheadEl.style.display = "none";
    return;
  }
  playheadEl.style.display = "block";
  playheadEl.style.left = x + "px";
}

/* ---------------- 播放器 ---------------- */

function togglePlay() {
  if (!video.src) { toast("请先加载媒体文件", "error"); return; }
  if (video.paused) video.play(); else video.pause();
}

function seekTo(t) {
  if (!video.src) return;
  video.currentTime = clamp(t, 0, isFinite(video.duration) ? video.duration : t);
}

function playCue(i) {
  const c = state.cues[i];
  if (!c || !video.src) { toast("未加载媒体，无法预览", "error"); return; }
  video.currentTime = c.start;
  state.playCueEnd = c.end;
  video.play();
}

function updateTimeDisplay() {
  const d = isFinite(video.duration) ? video.duration : 0;
  $("timeDisplay").textContent = `${fmtTime(video.currentTime)} / ${fmtTime(d)}`;
}

//: 字幕叠加层样式（字号 px / 距底部百分比），持久化到 localStorage
const LS_SUB_FONT = "subtitle-studio:fontSize";
const LS_SUB_POS = "subtitle-studio:subPos";
//: 预览自动隐藏时长（拖动滑块时）
const PREVIEW_HIDE_MS = 1800;

//: localStorage 安全读写：隐私模式/禁用存储时静默降级，避免初始化崩溃白屏
function lsGet(key, fallback) {
  try {
    const v = localStorage.getItem(key);
    return v === null ? fallback : v;
  } catch (_) {
    return fallback;
  }
}

function lsSet(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch (_) {
    /* 隐私模式等场景下忽略 */
  }
}

const subStyle = {
  fontSize: Number(lsGet(LS_SUB_FONT, "")) || 19,
  bottomPct: Number(lsGet(LS_SUB_POS, "")) || 7,
};
let subPreviewTimer = null;

function applySubStyle() {
  overlayEl.style.fontSize = subStyle.fontSize + "px";
  overlayEl.style.bottom = subStyle.bottomPct + "%";
  $("lblFontSize").textContent = subStyle.fontSize + "px";
  $("lblSubPos").textContent = subStyle.bottomPct + "%";
  $("rngFontSize").setAttribute("aria-valuetext", subStyle.fontSize + "像素");
  $("rngSubPos").setAttribute("aria-valuetext", "距底部 " + subStyle.bottomPct + "%");
}

function persistSubStyle() {
  lsSet(LS_SUB_FONT, String(subStyle.fontSize));
  lsSet(LS_SUB_POS, String(subStyle.bottomPct));
}

function showOverlayText(text) {
  overlayEl.textContent = text;
  overlayEl.classList.remove("hidden");
}

function hideOverlay() {
  overlayEl.classList.add("hidden");
}

function previewSubStyle() {
  // 调节滑块时即时预览：优先当前播放位置字幕，其次选中/第一条；都没有则用占位文本
  const i = state.activeIdx >= 0 ? state.activeIdx
    : (state.selected >= 0 ? state.selected : (state.cues.length ? 0 : -1));
  const text = i >= 0 ? state.cues[i].text : "字幕预览效果";
  showOverlayText(text);
  clearTimeout(subPreviewTimer);
  subPreviewTimer = setTimeout(() => {
    // 预览结束：仅在「无有效字幕」或「未勾选显示」时隐藏，避免覆盖正常显示
    if (state.activeIdx < 0 || !$("chkShowSub").checked) hideOverlay();
  }, PREVIEW_HIDE_MS);
}

function bindSubStyleControls() {
  const fs = $("rngFontSize");
  const pos = $("rngSubPos");
  fs.value = String(subStyle.fontSize);
  pos.value = String(subStyle.bottomPct);
  fs.addEventListener("input", () => {
    subStyle.fontSize = Number(fs.value);
    applySubStyle();
    previewSubStyle();
  });
  pos.addEventListener("input", () => {
    subStyle.bottomPct = Number(pos.value);
    applySubStyle();
    previewSubStyle();
  });
  for (const el of [fs, pos]) {
    el.addEventListener("change", persistSubStyle);
  }
}

function updateOverlay() {
  const i = state.activeIdx;
  const show = i >= 0 && $("chkShowSub").checked && !!video.src;
  if (show) {
    showOverlayText(state.cues[i].text);
  } else {
    hideOverlay();
  }
}

function updateActiveCue() {
  if (!video.src) {
    if (state.activeIdx !== -1) { state.activeIdx = -1; applyRowStates(); updateOverlay(); }
    return;
  }
  const t = video.currentTime;
  let idx = -1;
  for (let i = 0; i < state.cues.length; i++) {
    if (t >= state.cues[i].start && t < state.cues[i].end) { idx = i; break; }
    if (state.cues[i].start > t) break;
  }
  if (idx !== state.activeIdx) {
    state.activeIdx = idx;
    applyRowStates();
    updateOverlay();
    if (idx >= 0 && video.paused === false) {
      const row = cueListEl.querySelector(`.cue-row[data-idx="${idx}"]`);
      if (row) row.scrollIntoView({ block: "nearest" });
    }
  }
}

function tick() {
  if (video.src) {
    updateTimeDisplay();
    updatePlayhead();
    updateActiveCue();
    if (state.playCueEnd != null && video.currentTime >= state.playCueEnd) {
      video.pause();
      state.playCueEnd = null;
    }
  }
  requestAnimationFrame(tick);
}

/* ---------------- 字幕操作 ---------------- */

function selectCue(i) {
  state.selected = i;
  applyRowStates();
  drawTimeline();
  const row = cueListEl.querySelector(`.cue-row[data-idx="${i}"]`);
  if (row) row.scrollIntoView({ block: "nearest" });
}

function addCue() {
  const t = video.src ? video.currentTime : 0;
  let insertAt = state.cues.length;
  for (let i = 0; i < state.cues.length; i++) {
    if (state.cues[i].end <= t + 0.001) insertAt = i + 1; else break;
  }
  const next = state.cues[insertAt];
  let end = t + 2;
  if (next && next.start < end + 0.05) end = Math.max(t + 0.2, next.start - 0.05);
  const cue = { start: round3(t), end: round3(end), text: "" };
  snapshot();
  state.cues.splice(insertAt, 0, cue);
  state.selected = insertAt;
  afterStructuralChange();
  const ta = cueListEl.querySelector(`.cue-row[data-idx="${insertAt}"] .cue-text`);
  if (ta) ta.focus();
}

function deleteSelected() {
  if (state.selected < 0 || state.selected >= state.cues.length) return;
  snapshot();
  state.cues.splice(state.selected, 1);
  state.selected = Math.min(state.selected, state.cues.length - 1);
  afterStructuralChange();
}

function mergeNext() {
  const i = state.selected;
  if (i < 0 || i >= state.cues.length - 1) { toast("没有可合并的下一条", "error"); return; }
  snapshot();
  const a = state.cues[i], b = state.cues[i + 1];
  a.end = Math.max(a.end, b.end);
  a.text = (a.text + "\n" + b.text).replace(/^\n+/, "");
  state.cues.splice(i + 1, 1);
  afterStructuralChange();
}

function splitAtPlayhead() {
  const i = state.selected;
  if (i < 0) { toast("请先选中一条字幕", "error"); return; }
  const c = state.cues[i];
  const t = video.src ? video.currentTime : null;
  if (t == null || t <= c.start + 0.05 || t >= c.end - 0.05) {
    toast("请把播放头移到选中条目内部再拆分", "error");
    return;
  }
  snapshot();
  const frac = (t - c.start) / (c.end - c.start);
  const cut = clamp(Math.round(c.text.length * frac), 1, Math.max(1, c.text.length - 1));
  state.cues.splice(i, 1,
    { start: c.start, end: round3(t), text: c.text.slice(0, cut) },
    { start: round3(t), end: c.end, text: c.text.slice(cut) });
  state.selected = i;
  afterStructuralChange();
  const ta = cueListEl.querySelector(`.cue-row[data-idx="${i + 1}"] .cue-text`);
  if (ta) ta.focus();
}

function splitTextAtPunct(text) {
  const parts = [];
  for (const line of String(text).split("\n")) {
    const m = line.match(/[^。！？；!?;….]+[。！？；!?;….]*/g);
    if (m) parts.push(...m.map((s) => s.trim()).filter(Boolean));
    else if (line.trim()) parts.push(line.trim());
  }
  return parts.length ? parts : [String(text)];
}

function punctSplitAll() {
  let changed = 0;
  snapshot();
  const out = [];
  for (const c of state.cues) {
    const parts = splitTextAtPunct(c.text);
    if (parts.length <= 1) { out.push({ ...c }); continue; }
    changed++;
    const total = parts.reduce((a, p) => a + p.length, 0) || 1;
    const dur = c.end - c.start;
    let t = c.start;
    parts.forEach((p, k) => {
      const isLast = k === parts.length - 1;
      const s = clamp(t, c.start, Math.max(c.start, c.end - 0.1));
      const e = isLast ? c.end
        : clamp(t + Math.max(0.2, dur * p.length / total), s + 0.1, c.end);
      out.push({ start: round3(s), end: round3(e), text: p });
      t = e;
    });
  }
  state.cues = out;
  state.selected = -1;
  afterStructuralChange();
  toast(changed ? `已按标点拆分 ${changed} 条` : "没有需要拆分的长条目", changed ? "success" : "");
}

function setStartAtPlayhead() {
  const i = state.selected;
  if (i < 0 || !video.src) return;
  snapshot();
  state.cues[i].start = round3(clamp(video.currentTime, 0, state.cues[i].end - 0.05));
  afterStructuralChange();
}

function setEndAtPlayhead() {
  const i = state.selected;
  if (i < 0 || !video.src) return;
  snapshot();
  state.cues[i].end = round3(Math.max(state.cues[i].start + 0.05, video.currentTime));
  afterStructuralChange();
}

/* ---------------- 自动保存 ---------------- */

function scheduleSave() {
  if (!state.job) return;
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(saveNow, SAVE_DEBOUNCE_MS);
}

async function saveNow() {
  if (!state.job) return;
  if (state.saving) { state.savePending = true; return; }
  state.saving = true;
  try {
    await api(`/api/jobs/${state.job.id}/segments`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ segments: state.cues }),
    });
  } catch (e) {
    toast("自动保存失败：" + e.message, "error");
  } finally {
    state.saving = false;
    if (state.savePending) { state.savePending = false; scheduleSave(); }
  }
}

window.addEventListener("beforeunload", () => {
  if (!state.job) return;
  fetch(`/api/jobs/${state.job.id}/segments`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ segments: state.cues }),
    keepalive: true,
  }).catch(() => {});
});

/* ---------------- 任务加载 / 轮询 ---------------- */

function loadJobData(job) {
  state.job = { id: job.id, kind: job.kind, filename: job.filename, has_media: job.has_media };
  state.cues = (job.segments || []).map((c) => ({ start: c.start, end: c.end, text: c.text }));
  state.selected = state.cues.length ? 0 : -1;
  state.history.length = 0;
  state.future.length = 0;
  lsSet(LS_LAST_JOB, job.id);  if (job.has_media) {
    // 切回服务端媒体时释放本地 object URL，避免泄漏
    if (state.localVideoUrl) {
      URL.revokeObjectURL(state.localVideoUrl);
      state.localVideoUrl = null;
    }
    video.src = `/api/media/${job.id}`;
    video.currentTime = 0;
  }
  computeOverlaps();
  renderList();
  drawTimeline();
  updateStats();
  updateStateLayers();
}

function showGlobalProgress(show, text = "", pct = 0) {
  globalProgress.classList.toggle("hidden", !show);
  if (show) {
    globalProgressBar.style.width = Math.round(pct * 100) + "%";
    globalProgressText.textContent = text;
  }
}

function startPolling(jobId) {
  stopPolling();
  let retry = 0;
  state.pollTimer = setInterval(async () => {
    let job;
    try {
      job = await api(`/api/jobs/${jobId}`);
      retry = 0;
    } catch (err) {
      // 网络抖动/服务繁忙：退避重试；任务不存在（404）才停止
      retry += 1;
      if (err.message.includes("404") || retry > POLL_MAX_RETRY) {
        stopPolling();
        showGlobalProgress(false);
        toast("识别状态获取失败：" + err.message, "error");
      } else if (retry === 3) {
        toast("网络不稳定，正在重试获取识别进度…", "");
      }
      return;
    }
    if (job.status === "done") {
      stopPolling();
      showGlobalProgress(false);
      loadJobData(job);
      hideModal("recognizeModal");
      $("btnRecStart").disabled = false;
      $("btnRecCancel").textContent = "取消";
      toast(`识别完成，共 ${job.segments.length} 条字幕`, "success");
    } else if (job.status === "error") {
      stopPolling();
      showGlobalProgress(false);
      hideModal("recognizeModal");
      $("btnRecStart").disabled = false;
      $("btnRecCancel").textContent = "取消";
      toast(job.message || "识别失败", "error");
    } else {
      // 弹窗关闭后主界面也能看到进度
      showGlobalProgress(true, job.message || "识别中…", job.progress);
      $("recProgressWrap").classList.remove("hidden");
      $("recProgressBar").style.width = Math.round(job.progress * 100) + "%";
      $("recStatus").textContent = job.message || "识别中…";
    }
  }, POLL_INTERVAL_MS);
}

function stopPolling() {
  if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
  showGlobalProgress(false);
}

function updateStateLayers() {
  const hasMedia = !!(state.job && state.job.has_media) || !!state.localVideoUrl;
  $("emptyState").classList.toggle("hidden", state.cues.length > 0 || hasMedia);
  $("noMediaHint").classList.toggle("hidden", hasMedia || state.cues.length === 0);
}

/* ---------------- 时间轴交互 ---------------- */

let drag = null;

function hitTestCue(t, x) {
  for (let i = 0; i < state.cues.length; i++) {
    const c = state.cues[i];
    const x1 = tlX(c.start), x2 = tlX(c.end);
    if (x >= x1 - 4 && x <= x2 + 4 && t >= c.start - 0.001 && t <= c.end + 0.001) {
      let edge = null;
      if (x2 - x1 > 16) edge = x <= x1 + 5 ? "l" : x >= x2 - 5 ? "r" : null;
      return { idx: i, edge };
    }
  }
  return null;
}

function updateCursor(x) {
  const t = tlT(x);
  const hit = hitTestCue(t, x);
  // 色块主体：点击跳转（pointer）；边缘：拖改时长（ew-resize）
  canvas.style.cursor = !hit ? "crosshair" : hit.edge ? "ew-resize" : "pointer";
}

//: 拖动阈值（像素）：超过才算拖动，否则视为点击。
//: 避免「想跳转到字幕处」变成「挪动了字幕」。
const DRAG_THRESHOLD_PX = 5;

canvas.addEventListener("pointerdown", (e) => {
  if (e.button !== 0) return;  // 仅主键（鼠标左键 / 触摸）
  const rect = canvas.getBoundingClientRect();
  const x = e.clientX - rect.left;
  const y = e.clientY - rect.top;
  const t = tlT(x);
  const hit = hitTestCue(t, x);
  if (!hit) {
    // 空白区域：按下即准备跳转（松手时跳到最终位置）
    drag = { mode: "seek", t, x0: x, y0: y };
    return;
  }
  // 色块：先选中；按住拖动超过阈值才进入移动/改时长，
  // 未超过阈值则松手时跳转到点击处（播放中选点的主要路径）
  selectCue(hit.idx);
  drag = {
    mode: "pending",
    intent: hit.edge ? "resize-" + hit.edge : "move",
    idx: hit.idx,
    t,
    t0: t,
    x0: x,
    y0: y,
    orig: { ...state.cues[hit.idx] },
    cueRef: state.cues[hit.idx],
    snapTaken: false,
  };
  // 指针捕获：在窗口外松手也能收到 pointerup，避免“幽灵拖动”
  try { canvas.setPointerCapture(e.pointerId); } catch (_) { /* 旧浏览器忽略 */ }
  canvas.style.cursor = drag.intent === "move" ? "grabbing" : "ew-resize";
});

window.addEventListener("pointermove", (e) => {
  if (!drag) return;
  const rect = canvas.getBoundingClientRect();
  const x = e.clientX - rect.left;
  const y = e.clientY - rect.top;
  const t = tlT(x);
  if (drag.mode === "seek") {
    drag.t = t;  // 跟随指针，松手跳到最后位置
    return;
  }
  if (drag.mode === "pending") {
    // 水平+垂直位移都计入阈值：纯竖向小幅抖动不算拖动
    if (Math.hypot(x - drag.x0, y - drag.y0) <= DRAG_THRESHOLD_PX) return;
    drag.mode = drag.intent;  // 超过阈值：升级为拖动
  }
  const cue = state.cues[drag.idx];
  if (!cue) { drag = null; return; }
  // 真正发生拖动时才记录撤销快照，避免单击产生空撤销步
  if (!drag.snapTaken) {
    snapshot();
    drag.snapTaken = true;
  }
  const dt = t - drag.t0;
  if (drag.mode === "move") {    cue.start = round3(Math.max(0, drag.orig.start + dt));
    cue.end = round3(drag.orig.end + dt);
  } else if (drag.mode === "resize-l") {
    cue.start = round3(clamp(drag.orig.start + dt, 0, cue.end - 0.05));
  } else {
    cue.end = round3(Math.max(cue.start + 0.05, drag.orig.end + dt));
  }
  updateRowInputs(drag.idx);
  computeOverlaps();
  applyRowStates();
  drawTimeline();
});

window.addEventListener("pointerup", () => {
  if (!drag) return;
  if (drag.mode === "seek") {
    seekTo(drag.t);
  } else if (drag.mode === "pending") {
    // 色块上单击（未拖动）：跳转到点击处，便于播放中选点
    seekTo(drag.t);
  } else {
    // 真实拖动结束：拖动可能打乱时间顺序，按开始时间排序维持「有序」不变量
    // （重叠检测与播放定位都依赖该不变量）
    const before = state.cues.map((c) => c.start).join(",");
    state.cues.sort((a, b) => a.start - b.start);
    if (state.cues.map((c) => c.start).join(",") !== before) {
      state.selected = state.cues.findIndex((c) => c === drag.cueRef);
      renderList();
    }
    updateStats();
    scheduleSave();
  }
  drag = null;
  canvas.style.cursor = "";  // 复位为 CSS 默认（crosshair）
});

// 兜底：窗口失焦或指针捕获被系统取消时放弃当前拖拽，
// 避免回到窗口后“无按键移动鼠标”继续改字幕
window.addEventListener("blur", () => { drag = null; canvas.style.cursor = ""; });
window.addEventListener("pointercancel", () => { drag = null; canvas.style.cursor = ""; });

canvas.addEventListener("mousemove", (e) => {
  if (drag) return;
  const rect = canvas.getBoundingClientRect();
  updateCursor(e.clientX - rect.left);
});

canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const x = e.clientX - rect.left;
  if (e.ctrlKey || e.metaKey) {
    const anchorT = tlT(x);
    const factor = e.deltaY < 0 ? 1.25 : 0.8;
    tl.zoom = clamp(tl.zoom * factor, 1, 200);
    updateTlGeom();
    tl.viewStart = clamp(anchorT - (x / tl.w) * tl.view, 0,
      Math.max(0, tl.duration - tl.view));
  } else {
    const dir = e.deltaY !== 0 ? e.deltaY : e.deltaX;
    tl.viewStart = clamp(tl.viewStart + dir * 0.002 * tl.view, 0,
      Math.max(0, tl.duration - tl.view));
  }
  drawTimeline();
}, { passive: false });

canvas.addEventListener("dblclick", () => {
  tl.zoom = 1;
  tl.viewStart = 0;
  drawTimeline();
});

// 自定义滚动条
let sbDrag = false;
function sbMove(e) {
  const rect = scrollbarEl.getBoundingClientRect();
  const frac = clamp((e.clientX - rect.left) / rect.width, 0, 1);
  tl.viewStart = clamp(frac * tl.duration - tl.view / 2, 0,
    Math.max(0, tl.duration - tl.view));
  drawTimeline();
}
scrollbarEl.addEventListener("pointerdown", (e) => {
  sbDrag = true;
  scrollbarEl.setPointerCapture(e.pointerId);
  sbMove(e);
});
scrollbarEl.addEventListener("pointermove", (e) => { if (sbDrag) sbMove(e); });
scrollbarEl.addEventListener("pointerup", () => { sbDrag = false; });
scrollbarEl.addEventListener("pointercancel", () => { sbDrag = false; });

/* ---------------- 列表编辑事件 ---------------- */

cueListEl.addEventListener("focusin", (e) => {
  if (e.target.classList.contains("cue-time") || e.target.classList.contains("cue-text")) {
    if (!state.editSnapshotTaken) {
      snapshot();
      state.editSnapshotTaken = true;
    }
  }
});

cueListEl.addEventListener("focusout", (e) => {
  if (e.target.classList && (e.target.classList.contains("cue-time") ||
      e.target.classList.contains("cue-text"))) {
    state.editSnapshotTaken = false;
  }
});

cueListEl.addEventListener("input", (e) => {
  const row = e.target.closest(".cue-row");
  if (!row) return;
  const i = +row.dataset.idx;
  const cue = state.cues[i];
  if (!cue) return;

  if (e.target.classList.contains("cue-text")) {
    cue.text = e.target.value;
    scheduleSave();
    return;
  }
  if (!e.target.classList.contains("cue-time")) return;

  const v = parseTime(e.target.value);
  if (v == null) { e.target.classList.add("invalid"); return; }
  e.target.classList.remove("invalid");
  const field = e.target.dataset.field;
  if (field === "start") {
    cue.start = round3(v);
    if (cue.end < cue.start + 0.001) cue.end = round3(cue.start + 0.001);
  } else {
    cue.end = round3(v);
    if (cue.end < cue.start + 0.001) cue.start = round3(cue.end - 0.001);
  }
  const other = row.querySelector(`.cue-time[data-field="${field === "start" ? "end" : "start"}"]`);
  if (other) other.value = fmtTime(field === "start" ? cue.end : cue.start);
  row.querySelector(".cue-dur").textContent = (cue.end - cue.start).toFixed(2) + "s";
  computeOverlaps();
  applyRowStates();
  drawTimeline();
  updateStats();
  scheduleSave();
});

cueListEl.addEventListener("click", (e) => {
  const row = e.target.closest(".cue-row");
  if (!row) return;
  const i = +row.dataset.idx;
  if (e.target.classList.contains("cue-play")) {
    selectCue(i);
    playCue(i);
    return;
  }
  selectCue(i);
});

cueListEl.addEventListener("dblclick", (e) => {
  const row = e.target.closest(".cue-row");
  if (!row) return;
  const i = +row.dataset.idx;
  selectCue(i);
  playCue(i);
});

/* ---------------- 播放器事件 ---------------- */

$("btnPlay").addEventListener("click", togglePlay);
video.addEventListener("click", togglePlay);
video.addEventListener("play", () => { $("btnPlay").textContent = "⏸"; });
video.addEventListener("pause", () => { $("btnPlay").textContent = "▶"; });
video.addEventListener("loadedmetadata", () => { drawTimeline(); });
video.addEventListener("durationchange", () => { drawTimeline(); });
video.addEventListener("error", () => {
  if (video.src) toast("浏览器无法播放该媒体格式（识别不受影响），可转为 MP4 后预览", "error");
});
$("selRate").addEventListener("change", (e) => { video.playbackRate = parseFloat(e.target.value); });
$("chkShowSub").addEventListener("change", updateOverlay);

/* ---------------- 工具栏 ---------------- */

$("btnAdd").addEventListener("click", addCue);
$("btnDelete").addEventListener("click", deleteSelected);
$("btnMerge").addEventListener("click", mergeNext);
$("btnSplit").addEventListener("click", splitAtPlayhead);
$("btnPunctSplit").addEventListener("click", punctSplitAll);
$("btnShift").addEventListener("click", () => {
  if (!state.cues.length) { toast("没有字幕可平移", "error"); return; }
  $("shiftOffset").value = "0";
  showModal("shiftModal");
});
$("btnShiftApply").addEventListener("click", () => {
  const offset = parseFloat($("shiftOffset").value);
  hideModal("shiftModal");
  if (!isFinite(offset) || offset === 0) return;
  const from = $("shiftScope").value === "from" ? Math.max(0, state.selected) : 0;
  snapshot();
  for (let i = from; i < state.cues.length; i++) {
    state.cues[i].start = round3(Math.max(0, state.cues[i].start + offset));
    state.cues[i].end = round3(Math.max(0, state.cues[i].end + offset));
    if (state.cues[i].end <= state.cues[i].start) {
      state.cues[i].end = round3(state.cues[i].start + 0.1);
    }
  }
  afterStructuralChange();
  toast(`已平移 ${offset > 0 ? "+" : ""}${offset}s`, "success");
});
$("btnShiftCancel").addEventListener("click", () => hideModal("shiftModal"));

/* ---------------- 弹窗 ---------------- */

function showModal(id) { $(id).classList.remove("hidden"); }
function hideModal(id) { $(id).classList.add("hidden"); }

document.querySelectorAll(".modal-mask").forEach((mask) => {
  mask.addEventListener("click", (e) => { if (e.target === mask) mask.classList.add("hidden"); });
});

$("btnHelp").addEventListener("click", () => showModal("helpModal"));
$("btnHelpClose").addEventListener("click", () => hideModal("helpModal"));

/* ---------------- 语音识别 ---------------- */

function openRecognize() {
  state.recFile = null;
  $("recFileName").classList.add("hidden");
  $("btnRecStart").disabled = true;
  $("btnRecCancel").textContent = "取消";
  $("recProgressWrap").classList.add("hidden");
  $("recProgressBar").style.width = "0%";
  $("recStatus").textContent = "";
  showModal("recognizeModal");
}

function setRecFile(f) {
  state.recFile = f;
  $("recFileName").textContent = `${f.name}（${(f.size / 1048576).toFixed(1)} MB）`;
  $("recFileName").classList.remove("hidden");
  $("btnRecStart").disabled = false;
}

$("btnRecognize").addEventListener("click", openRecognize);
$("btnEmptyRecognize").addEventListener("click", openRecognize);
$("recDrop").addEventListener("click", () => $("fileRecognize").click());
$("fileRecognize").addEventListener("change", (e) => {
  if (e.target.files[0]) setRecFile(e.target.files[0]);
  e.target.value = "";
});
["dragover", "dragleave", "drop"].forEach((ev) => {
  $("recDrop").addEventListener(ev, (e) => {
    e.preventDefault();
    $("recDrop").classList.toggle("dragover", ev === "dragover");
    if (ev === "drop" && e.dataTransfer.files[0]) setRecFile(e.dataTransfer.files[0]);
  });
});

$("btnRecStart").addEventListener("click", async () => {
  if (!state.recFile) return;
  const fd = new FormData();
  fd.append("file", state.recFile);
  fd.append("model", $("selModel").value);
  fd.append("language", $("selLanguage").value);
  fd.append("task", $("selTask").value);
  $("btnRecStart").disabled = true;
  $("btnRecCancel").textContent = "后台运行";
  $("recProgressWrap").classList.remove("hidden");
  $("recStatus").textContent = "正在上传…";
  $("recProgressBar").style.width = "5%";
  try {
    const { job_id } = await api("/api/transcribe", { method: "POST", body: fd });
    state.job = { id: job_id, kind: "transcribe", filename: state.recFile.name, has_media: true };
    lsSet(LS_LAST_JOB, job_id);
    video.src = `/api/media/${job_id}`;
    updateStateLayers();
    startPolling(job_id);
  } catch (err) {
    toast("启动识别失败：" + err.message, "error");
    $("btnRecStart").disabled = false;
    $("btnRecCancel").textContent = "取消";
  }
});

$("btnRecCancel").addEventListener("click", () => {
  hideModal("recognizeModal");
  if (state.pollTimer) toast("识别仍在后台进行，完成后会自动加载", "");
});

/* ---------------- 导入 / 导出 / 本地视频 ---------------- */

$("btnImport").addEventListener("click", () => $("fileImport").click());
$("btnEmptyImport").addEventListener("click", () => $("fileImport").click());
$("fileImport").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  e.target.value = "";
  if (!f) return;
  const fd = new FormData();
  fd.append("file", f);
  try {
    const r = await api("/api/import", { method: "POST", body: fd });
    const job = await api(`/api/jobs/${r.job_id}`);
    loadJobData(job);
    toast(`已导入 ${r.count} 条字幕`, "success");
  } catch (err) {
    toast("导入失败：" + err.message, "error");
  }
});

$("btnLocalVideo").addEventListener("click", () => $("fileLocalVideo").click());
$("btnHintLocalVideo").addEventListener("click", () => $("fileLocalVideo").click());
$("fileLocalVideo").addEventListener("change", (e) => {
  const f = e.target.files[0];
  e.target.value = "";
  if (!f) return;
  if (state.localVideoUrl) URL.revokeObjectURL(state.localVideoUrl);
  state.localVideoUrl = URL.createObjectURL(f);
  video.src = state.localVideoUrl;
  updateStateLayers();
  toast(`已加载本地媒体：${f.name}`, "success");
});

$("btnExport").addEventListener("click", () => $("exportDropdown").classList.toggle("open"));
document.addEventListener("click", (e) => {
  if (!e.target.closest("#exportDropdown")) $("exportDropdown").classList.remove("open");
});
$("exportMenu").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-fmt]");
  if (!btn) return;
  $("exportDropdown").classList.remove("open");
  if (!state.job || state.cues.length === 0) { toast("没有可导出的字幕", "error"); return; }
  const a = document.createElement("a");
  a.href = `/api/jobs/${state.job.id}/export?format=${btn.dataset.fmt}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
});

/* ---------------- 整窗拖拽 ---------------- */

["dragover", "drop"].forEach((ev) => {
  window.addEventListener(ev, (e) => e.preventDefault());
});
window.addEventListener("drop", (e) => {
  const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
  if (!f) return;
  if (/\.(srt|vtt)$/i.test(f.name)) {
    const dt = new DataTransfer();
    dt.items.add(f);
    $("fileImport").files = dt.files;
    $("fileImport").dispatchEvent(new Event("change"));
  } else {
    setRecFile(f);
    openRecognize();
  }
});

/* ---------------- 键盘快捷键 ---------------- */

document.addEventListener("keydown", (e) => {
  if (document.querySelector(".modal-mask:not(.hidden)")) return;
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName);
  const mod = e.ctrlKey || e.metaKey;

  if (mod && e.key.toLowerCase() === "z" && !e.shiftKey) { e.preventDefault(); undo(); return; }
  if (mod && (e.key.toLowerCase() === "y" || (e.key.toLowerCase() === "z" && e.shiftKey))) {
    e.preventDefault(); redo(); return;
  }
  if (typing) return;

  switch (e.code) {
    case "Space": e.preventDefault(); togglePlay(); break;
    case "ArrowLeft": e.preventDefault(); seekTo(video.currentTime - (e.shiftKey ? 10 : 2)); break;
    case "ArrowRight": e.preventDefault(); seekTo(video.currentTime + (e.shiftKey ? 10 : 2)); break;
    case "Enter": if (state.selected >= 0) playCue(state.selected); break;
    case "Delete": case "Backspace": deleteSelected(); break;
    case "BracketLeft": setStartAtPlayhead(); break;
    case "BracketRight": setEndAtPlayhead(); break;
    default: break;
  }
});

/* ---------------- 初始化 ---------------- */

async function populateSelects() {
  let cfg;
  try {
    cfg = await api("/api/config");
  } catch (err) {
    toast("获取识别配置失败：" + err.message + "，请刷新重试", "error");
    return;
  }
  const fill = (sel, items, defaultId) => {
    sel.innerHTML = items.map((i) =>
      `<option value="${esc(i.id)}"${i.id === defaultId ? " selected" : ""}>${esc(i.label)}</option>`).join("");
  };
  fill($("selModel"), cfg.models, "small");
  fill($("selLanguage"), cfg.languages, "");
  fill($("selTask"), cfg.tasks, "transcribe");
}

async function init() {
  await populateSelects();
  applySubStyle();
  bindSubStyleControls();
  resizeCanvas();
  drawTimeline();
  updateStats();

  const ro = new ResizeObserver(() => { resizeCanvas(); drawTimeline(); });
  ro.observe($("timelineWrap"));

  // 恢复上次任务（刷新不丢失）
  const lastId = lsGet(LS_LAST_JOB, "");
  if (lastId) {
    try {
      const job = await api(`/api/jobs/${lastId}`);
      if (job.status === "done" || job.kind === "import") {
        loadJobData(job);
        toast(`已恢复上次字幕：${job.filename}`, "");
      } else if (job.status === "running" || job.status === "queued") {
        state.job = { id: job.id, kind: job.kind, filename: job.filename, has_media: job.has_media };
        if (job.has_media) video.src = `/api/media/${job.id}`;
        showModal("recognizeModal");
        $("btnRecStart").disabled = true;
        $("btnRecCancel").textContent = "后台运行";
        $("recProgressWrap").classList.remove("hidden");
        startPolling(job.id);
      }
    } catch (_) { /* 任务已删除，忽略 */ }
  }

  updateStateLayers();
  requestAnimationFrame(tick);
}

init();
})();
