const test = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const script = readFileSync(path.resolve(__dirname, "../../src/primitive_vla/interfaces/web/static/components/robot-view.js"), "utf8");
const views = [{id: "front", label: "Front", is_default: false}, {id: "wrist", label: "Wrist", is_default: true}];
const frame = {id: "frame-1", captured_at: 1700000000, media_type: "image/jpeg", image: "YWJj", width: 10, height: 10};

function setup(t, catalog = views) {
  const dom = new JSDOM('<section id="view"></section>', {url: "http://localhost", runScripts: "outside-only", pretendToBeVisual: true});
  const { window } = dom;
  const streams = [], requests = [];
  window.HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  window.HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  window.EventSource = class {
    constructor(url) { this.url = url; this.handlers = {}; streams.push(this); }
    addEventListener(name, handler) { this.handlers[name] = handler; }
    close() { this.closed = true; }
    emit(name, data) { this.handlers[name]({data: JSON.stringify(data)}); }
  };
  window.fetch = async url => {
    requests.push(url);
    return {ok: true, json: async () => ({views: catalog})};
  };
  window.eval(script);
  const root = window.document.getElementById("view");
  const view = new window.PrimitiveRobotView(root);
  t.after(() => { view.destroy(); window.close(); });
  return {window, root, view, streams, requests};
}

test("Provider chooses default camera; enlargement shares the same live subscription", async t => {
  const ui = setup(t);
  await ui.view.setRobot("custom-robot", "Custom robot");
  assert.equal(ui.streams[0].url, "/api/robots/custom-robot/views/wrist/stream");
  ui.streams[0].emit("frame", frame);
  assert.equal(ui.root.dataset.state, "live");
  assert.equal(ui.view.time.dateTime, "2023-11-14T22:13:20.000Z");
  ui.view.expand.click();
  assert.equal(ui.view.dialog.open, true);
  assert.equal(ui.view.dialogImage.src, ui.view.image.src);
  assert.equal(ui.streams.length, 1);
  assert.deepEqual(ui.requests, ["/api/robots/custom-robot/views"]);
});

test("camera switching drops late frames and closes the old stream", async t => {
  const ui = setup(t);
  await ui.view.setRobot("robot", "Robot");
  const old = ui.streams[0];
  old.emit("frame", frame);
  ui.view.select.value = "front";
  ui.view.select.dispatchEvent(new ui.window.Event("change"));
  assert.equal(old.closed, true);
  assert.equal(ui.view.image.hidden, true);
  old.emit("frame", {...frame, image: "c3RhbGU="});
  assert.equal(ui.view.image.hidden, true);
  assert.match(ui.streams[1].url, /\/front\/stream$/);
  ui.streams[1].emit("frame", {...frame, image: "ZnJlc2g="});
  assert.match(ui.view.image.src, /ZnJlc2g=$/);
});

test("collapse, hidden inspector and minimized document unsubscribe until visible", async t => {
  const ui = setup(t);
  await ui.view.setRobot("robot");
  ui.root.querySelector(".robot-view-toggle").click();
  assert.equal(ui.streams[0].closed, true);
  ui.view.setVisible(false);
  ui.root.querySelector(".robot-view-toggle").click();
  assert.equal(ui.streams.length, 1);
  ui.view.setVisible(true);
  assert.equal(ui.streams.length, 2);
  Object.defineProperty(ui.window.document, "visibilityState", {value: "hidden", configurable: true});
  ui.window.document.dispatchEvent(new ui.window.Event("visibilitychange"));
  assert.equal(ui.streams[1].closed, true);
  Object.defineProperty(ui.window.document, "visibilityState", {value: "visible", configurable: true});
  ui.window.document.dispatchEvent(new ui.window.Event("visibilitychange"));
  assert.equal(ui.streams.length, 3);
});

test("preview errors retain the last capture time and wait for explicit reconnect", async t => {
  const ui = setup(t);
  await ui.view.setRobot("robot");
  ui.streams[0].emit("frame", frame);
  ui.streams[0].emit("view_error", {message: "camera offline"});
  assert.equal(ui.streams[0].closed, true);
  assert.equal(ui.root.dataset.state, "error");
  assert.match(ui.root.textContent, /camera offline/);
  assert.equal(ui.view.time.dateTime, "2023-11-14T22:13:20.000Z");
  ui.view.setVisible(false);
  ui.view.setVisible(true);
  assert.equal(ui.root.dataset.state, "error");
  assert.match(ui.root.textContent, /camera offline/);
  assert.equal(ui.streams.length, 1);
  ui.view.retry.click();
  assert.equal(ui.streams.length, 2);
  assert.equal(ui.view.retry.hidden, true);
});

test("a robot without view support does not create a stream", async t => {
  const ui = setup(t, []);
  await ui.view.setRobot("robot");
  assert.equal(ui.streams.length, 0);
  assert.equal(ui.view.select.disabled, true);
  assert.match(ui.root.textContent, /未提供机器人视角/);
});

test("switching robot aborts catalog requests and cannot restore the previous robot", async t => {
  const ui = setup(t);
  let resolveOld, oldSignal;
  ui.window.fetch = (url, {signal}) => {
    if (url.includes("/old/")) {
      oldSignal = signal;
      return new Promise(resolve => { resolveOld = resolve; });
    }
    return Promise.resolve({ok: true, json: async () => ({views})});
  };
  const old = ui.view.setRobot("old");
  await ui.view.setRobot("new");
  resolveOld({ok: true, json: async () => ({views: [{id: "obsolete", label: "Old", is_default: true}]})});
  await old;
  assert.equal(oldSignal.aborted, true);
  assert.equal(ui.streams.length, 1);
  assert.match(ui.streams[0].url, /\/new\/views\/wrist\/stream$/);
  assert.equal(ui.view.select.value, "wrist");
});
