/**
 * 前端时间轴交互回归测试（Node + 轻量 DOM 桩，无需浏览器）。
 *
 * 覆盖两类用户反馈问题：
 *   1. 点击时间轴色块 = 跳转到点击处（播放中选点），不得移动字幕；
 *   2. 只有拖动超过阈值（5px）才移动/改时长，且不得产生 NaN 时间。
 * 另覆盖：右键不触发拖动、字幕字号/位置滑块的即时预览与持久化。
 *
 * 运行：node tests/ui_logic_test.cjs
 */
"use strict";

const assert = require("assert");
const path = require("path");

// ---------------------------------------------------------------- DOM 桩

function makeEl(id) {
  const listeners = {};
  const el = {
    id,
    style: {},
    value: "",
    textContent: "",
    checked: true,
    disabled: false,
    clientWidth: 800,
    _innerHTML: "",
    _listeners: listeners,
    classList: {
      _set: new Set(id === "toast" ? ["hidden"] : []),
      add(...cs) { cs.forEach((c) => this._set.add(c)); },
      remove(...cs) { cs.forEach((c) => this._set.delete(c)); },
      toggle(c, force) {
        const on = force === undefined ? !this._set.has(c) : force;
        if (on) this._set.add(c); else this._set.delete(c);
        return on;
      },
      contains(c) { return this._set.has(c); },
    },
    setAttribute(k, v) { (this._attrs || (this._attrs = {}))[k] = v; },
    getAttribute(k) { return (this._attrs || {})[k]; },
    addEventListener(type, fn) { (listeners[type] || (listeners[type] = [])).push(fn); },
    removeEventListener() {},
    appendChild() {},
    click() {},
    focus() {},
    blur() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
    scrollIntoView() {},
    getBoundingClientRect() {
      return { left: 0, top: 0, width: 800, height: 92, right: 800, bottom: 92 };
    },
    setPointerCapture() {},
    releasePointerCapture() {},
  };
  Object.defineProperty(el, "innerHTML", {
    get() { return this._innerHTML; },
    set(v) { this._innerHTML = v; },
  });
  return el;
}

const ctxStub = new Proxy({}, {
  get: (t, p) => (typeof p === "string" ? () => {} : undefined),
  set: () => true,
});

const canvasEl = makeEl("timeline");
canvasEl.width = 800;
canvasEl.height = 92;
canvasEl.getContext = () => ctxStub;

const videoEl = makeEl("video");
videoEl.currentTime = 0;
videoEl.duration = 30;
videoEl.paused = true;
videoEl.src = "";
videoEl.play = () => {};
videoEl.pause = () => {};

const els = new Map();
function getEl(id) {
  if (!els.has(id)) {
    els.set(id, id === "timeline" ? canvasEl : id === "video" ? videoEl : makeEl(id));
  }
  return els.get(id);
}

const winListeners = {};
const docListeners = {};

globalThis.window = {
  addEventListener: (t, fn) => { (winListeners[t] || (winListeners[t] = [])).push(fn); },
  devicePixelRatio: 1,
};
globalThis.document = {
  getElementById: getEl,
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: (t, fn) => { (docListeners[t] || (docListeners[t] = [])).push(fn); },
  createElement: () => makeEl("a"),
  body: makeEl("body"),
};

const store = new Map([["subtitle-studio:lastJob", "testjob"]]);
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
};

const FAKE_JOB = {
  id: "testjob",
  kind: "transcribe",
  filename: "t.mp3",
  status: "done",
  has_media: true,
  segments: [
    { start: 1.0, end: 3.0, text: "第一条" },
    { start: 4.0, end: 6.0, text: "第二条" },
    { start: 7.0, end: 9.5, text: "第三条" },
  ],
  settings: {},
};
const posts = [];
globalThis.fetch = async (url, opts) => {
  const u = String(url);
  if (u.includes("/api/config")) {
    return {
      ok: true,
      json: async () => ({
        models: [{ id: "tiny", label: "t" }, { id: "base", label: "b" }, { id: "small", label: "s" }],
        languages: [{ id: "", label: "自动" }, { id: "zh", label: "中文" }],
        tasks: [{ id: "transcribe", label: "识别" }, { id: "translate", label: "翻译" }],
        scripts: [{ id: "auto", label: "自动" }, { id: "simplified", label: "简体" }, { id: "traditional", label: "繁体" }],
      }),
    };
  }
  // 保存编辑结果的 POST：先于任务查询匹配，避免被 /api/jobs/testjob 前缀吞掉
  if (opts && opts.method === "POST" && u.includes("/segments")) {
    posts.push(JSON.parse(opts.body));
    return { ok: true, json: async () => ({ ok: true }) };
  }
  if (/\/api\/jobs\/testjob$/.test(u)) {
    return { ok: true, json: async () => FAKE_JOB };
  }
  return { ok: true, json: async () => ({ ok: true }) };
};

globalThis.ResizeObserver = class { observe() {} disconnect() {} };
globalThis.requestAnimationFrame = () => 0;
globalThis.URL = { createObjectURL: () => "blob:mock", revokeObjectURL() {} };

// ---------------------------------------------------------------- 加载应用

require(path.join(__dirname, "..", "static", "app.js"));

const fire = (el, type, ev) => {
  (el._listeners[type] || []).forEach((fn) => fn(ev));
};
const fireWin = (type, ev) => {
  (winListeners[type] || []).forEach((fn) => fn(ev));
};
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  await wait(80);  // 等 init() 的异步链路（fetch 配置 + 任务恢复）完成

  // 恢复后应加载 3 条字幕并挂上媒体（updateStats 写入 innerHTML）
  assert.strictEqual(videoEl.src, "/api/media/testjob", "应恢复任务媒体");
  assert.ok(
    String(getEl("stats").innerHTML).includes("3"),
    `应恢复 3 条字幕，实际统计: ${getEl("stats").innerHTML}`,
  );

  // 几何：duration=30s，画布宽 800 → 26.67 px/s；第二条字幕 4~6s → x∈[106.7,160]
  const PX_PER_SEC = 800 / 30;
  const xAt = (t) => t * PX_PER_SEC;
  const y = 50;

  // ---- 用例 1：色块上单击（位移 < 阈值）= 跳转，字幕不动 ----
  videoEl.currentTime = 0;
  fire(canvasEl, "pointerdown", { button: 0, clientX: xAt(5), clientY: y, pointerId: 1 });
  fireWin("pointermove", { clientX: xAt(5) + 2, clientY: y, pointerId: 1 });
  fireWin("pointerup", { clientX: xAt(5) + 2, clientY: y, pointerId: 1 });
  assert.ok(
    Math.abs(videoEl.currentTime - 5.0) < 0.15,
    `点击色块应跳转到 ~5s，实际 ${videoEl.currentTime}`,
  );
  assert.strictEqual(posts.length, 0, "单击不得触发保存（字幕未变）");

  // ---- 用例 2：拖动超过阈值 = 移动字幕，且时间不得为 NaN ----
  videoEl.currentTime = 0;
  fire(canvasEl, "pointerdown", { button: 0, clientX: xAt(5), clientY: y, pointerId: 1 });
  fireWin("pointermove", { clientX: xAt(5) + 3, clientY: y, pointerId: 1 });   // 未超阈值
  fireWin("pointermove", { clientX: xAt(5) + 40, clientY: y, pointerId: 1 });  // 超阈值 → 拖动
  fireWin("pointerup", { clientX: xAt(5) + 40, clientY: y, pointerId: 1 });
  await wait(1400);  // 等自动保存防抖
  assert.ok(posts.length >= 1, "拖动后应触发自动保存");
  const seg = posts[posts.length - 1].segments[1];
  assert.ok(Number.isFinite(seg.start) && Number.isFinite(seg.end), `时间不得为 NaN: ${JSON.stringify(seg)}`);
  assert.ok(Math.abs(seg.start - 5.5) < 0.15, `拖动后 start 应 ≈5.5，实际 ${seg.start}`);
  assert.ok(Math.abs(seg.end - 7.5) < 0.15, `拖动后 end 应 ≈7.5，实际 ${seg.end}`);

  // ---- 用例 3：右键不触发任何拖动/跳转 ----
  videoEl.currentTime = 0;
  const postsBefore = posts.length;
  fire(canvasEl, "pointerdown", { button: 2, clientX: xAt(5), clientY: y, pointerId: 1 });
  fireWin("pointermove", { clientX: xAt(5) + 40, clientY: y, pointerId: 1 });
  fireWin("pointerup", { clientX: xAt(5) + 40, clientY: y, pointerId: 1 });
  assert.strictEqual(videoEl.currentTime, 0, "右键不应跳转");
  await wait(1400);
  assert.strictEqual(posts.length, postsBefore, "右键不应修改字幕");

  // ---- 用例 4：字号/位置滑块即时预览 + 持久化 ----
  const fs = getEl("rngFontSize");
  const pos = getEl("rngSubPos");
  fs.value = "30";
  fire(fs, "input", { target: fs });
  assert.strictEqual(getEl("subOverlay").style.fontSize, "30px", "字号滑块应实时改 overlay");
  assert.strictEqual(getEl("lblFontSize").textContent, "30px");
  assert.strictEqual(getEl("subOverlay").classList.contains("hidden"), false, "调节时应预览");
  pos.value = "40";
  fire(pos, "input", { target: pos });
  assert.strictEqual(getEl("subOverlay").style.bottom, "40%", "位置滑块应实时改 overlay");
  assert.strictEqual(getEl("lblSubPos").textContent, "40%");
  fire(fs, "change", { target: fs });
  fire(pos, "change", { target: pos });
  assert.strictEqual(store.get("subtitle-studio:fontSize"), "30", "字号应持久化");
  assert.strictEqual(store.get("subtitle-studio:subPos"), "40", "位置应持久化");

  console.log("UI-LOGIC-OK (click-seek / drag-move / right-click / sliders)");
  process.exit(0);
})().catch((err) => {
  console.error("UI-LOGIC-FAILED:", err && err.message ? err.message : err);
  process.exit(1);
});
