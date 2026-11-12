const test = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const { setTimeout: delay } = require("node:timers/promises");
const { JSDOM } = require("jsdom");

const staticDir = path.resolve(__dirname, "../../src/primitive_vla/interfaces/web/static");
const html = readFileSync(path.join(staticDir, "index.html"), "utf8");
const script = readFileSync(path.join(staticDir, "app.js"), "utf8");
const record = (payload = {}) => ({ id: "record-1", source: "planning", domain: "planning", event_type: "task_created", status: "draft", timestamp: 1700000000, payload, resource_refs: [], lifetime: "short", call_id: null });
const session = (goal = "Test task") => ({ id: "task-1", goal, status: "draft", robot_id: "", created_at: 1700000000, decisions: 0, tool_calls: 0, active_seconds: 0, last_result: {} });
const snapshot = s => ({ session: s, records: [record({ goal: s.goal })], active_calls: [], occupied: {}, runner_error: null, has_more: false });

async function until(predicate) {
  for (let i = 0; i < 100; i++) { if (predicate()) return; await delay(5); }
  throw new Error("DOM did not reach expected state");
}

async function setup({ connected = false, tools = [], records = [] } = {}) {
  const dom = new JSDOM(html, { url: "http://localhost/", runScripts: "outside-only", pretendToBeVisual: true });
  const requests = [], sessions = [], streams = [];
  const { window } = dom;
  window.HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  window.HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  window.EventSource = class {
    constructor(url) { this.url = url; this.handlers = {}; streams.push(this); }
    addEventListener(name, fn) { this.handlers[name] = fn; }
    close() { this.closed = true; }
    emit(data) { this.handlers.snapshot({ data: JSON.stringify(data) }); }
  };
  window.fetch = async (url, init = {}) => {
    const request = { url: String(url), method: init.method || "GET", body: init.body ? JSON.parse(init.body) : undefined };
    requests.push(request);
    let data;
    if (url === "/api/status") data = { connected, workspace: "/workspace", planner: connected ? "TestAdapter" : null, tools: tools.length, skills: 0, robots: connected ? [{ id: "r1", type: "test", label: "Test robot" }] : [], limits: null };
    else if (url.startsWith("/api/sessions") && request.method === "POST") { const created = session(request.body.goal); sessions.push(created); data = created; }
    else if (url.startsWith("/api/sessions?" ) || url === "/api/sessions") data = { sessions };
    else if (/^\/api\/sessions\/[^/]+\/history\?/.test(url)) data = { records };
    else if (url.startsWith("/api/sessions/")) data = snapshot(sessions[0]);
    else if (url === "/api/tools") data = { tools };
    else if (url === "/api/skills") data = { skills: [] };
    else if (url.startsWith("/api/memory")) data = { records };
    else if (url.startsWith("/api/records/") || url.startsWith("/api/evidence/")) data = records[0] || record();
    else if (url.startsWith("/api/replies/")) data = snapshot({ ...sessions[0], status: "running" });
    else throw new Error(`Unexpected test request ${url}`);
    return new Response(JSON.stringify(data), { status: 200, headers: { "content-type": "application/json" } });
  };
  window.eval(script);
  await until(() => window.document.getElementById("server-label").textContent !== "正在连接工作台");
  return { dom, window, document: window.document, requests, sessions, streams };
}

test("home honestly shows disconnected state and starter prompts do not run actions", async t => {
  const ui = await setup(); t.after(() => ui.window.close());
  assert.match(ui.document.getElementById("connection-note").textContent, /Planning 尚未连接/);
  ui.document.querySelector("[data-prompt]").click();
  assert.match(ui.document.getElementById("task-input").value, /观察当前环境/);
  assert.equal(ui.document.getElementById("submit-label").textContent, "保存任务");
  assert.equal(ui.requests.filter(r => r.method === "POST").length, 0);
});

test("draft submission persists through the API, opens its session, and escapes user text", async t => {
  const ui = await setup(); t.after(() => ui.window.close());
  const goal = '<img src=x onerror="bad()"> Inspect my room';
  ui.document.getElementById("task-input").value = goal;
  ui.document.getElementById("task-form").dispatchEvent(new ui.window.Event("submit", { bubbles: true, cancelable: true }));
  await until(() => ui.document.getElementById("task-title").textContent === goal);
  assert.deepEqual(ui.requests.find(r => r.method === "POST").body, { goal, robot_id: "", start: false });
  assert.equal(ui.window.location.hash, "#session/task-1");
  assert.equal(ui.document.querySelector("#timeline img"), null);
  assert.equal(ui.document.querySelector('[data-control="resume"]').disabled, true);
});

test("connected submission chooses the selected real robot", async t => {
  const ui = await setup({ connected: true }); t.after(() => ui.window.close());
  ui.document.getElementById("task-input").value = "Inspect";
  ui.document.getElementById("task-form").dispatchEvent(new ui.window.Event("submit", { cancelable: true }));
  await until(() => ui.requests.some(r => r.method === "POST"));
  assert.deepEqual(ui.requests.find(r => r.method === "POST").body, { goal: "Inspect", robot_id: "r1", start: true });
  await until(() => ui.streams.length > 0 && ui.document.getElementById("task-title").textContent === "Inspect");
});

test("SSE updates a pending question and the reply preserves structured map input", async t => {
  const ui = await setup({ connected: true }); t.after(() => ui.window.close());
  ui.sessions.push(session()); ui.window.location.hash = "#session/task-1";
  await until(() => ui.streams.length > 0);
  ui.streams[0].emit(snapshot({ ...ui.sessions[0], status: "waiting_user", question: "选择位置" }));
  assert.equal(ui.document.getElementById("reply-form").hidden, false);
  assert.match(ui.document.getElementById("question-card").textContent, /选择位置/);
  ui.document.getElementById("reply-input").value = "Point B";
  ui.document.getElementById("toggle-payload").click();
  ui.document.getElementById("reply-payload").value = '{"xy":[2,3]}';
  ui.document.getElementById("reply-form").dispatchEvent(new ui.window.Event("submit", { cancelable: true }));
  await until(() => ui.requests.some(r => r.url.startsWith("/api/replies")));
  assert.deepEqual(ui.requests.find(r => r.url.startsWith("/api/replies")).body, { answer: "Point B", payload: { xy: [2, 3] } });
  await until(() => ui.document.getElementById("task-status").textContent === "运行中");
});

test("tool domain filters and contract details use the actual registry data", async t => {
  const tools = ["state", "execution"].map(domain => ({ name: `${domain}.custom`, domain, description: "Registered capability", input_schema: { type: "object" }, output_schema: {}, provider: "external", supported_robots: [], required_resources: [], execution_mode: "async", timeout: 60 }));
  const ui = await setup({ connected: true, tools }); t.after(() => ui.window.close());
  ui.window.location.hash = "#tools";
  await until(() => ui.document.querySelectorAll(".catalog-card").length === 2);
  ui.document.querySelector('[data-domain="state"]').click();
  await until(() => ui.document.querySelectorAll(".catalog-card").length === 1);
  ui.document.querySelector(".catalog-card").click();
  assert.equal(ui.document.getElementById("dialog-title").textContent, "state.custom");
  assert.match(ui.document.getElementById("dialog-body").textContent, /输入契约/);
});

test("Memory content is rendered as text, and navigation closes the previous SSE stream", async t => {
  const ui = await setup({ records: [record({ note: '<script>bad()</script>' })] }); t.after(() => ui.window.close());
  ui.sessions.push(session()); ui.window.location.hash = "#session/task-1";
  await until(() => ui.streams.length > 0);
  ui.window.location.hash = "#memory";
  await until(() => ui.document.querySelector(".record-row"));
  assert.equal(ui.streams[0].closed, true);
  assert.equal(ui.document.querySelector("#collection-results script"), null);
  ui.document.querySelector(".record-row").click();
  await until(() => ui.document.getElementById("detail-dialog").open);
  assert.match(ui.document.getElementById("dialog-body").textContent, /<script>bad/);
});

test("command search and theme switch work without a desktop bridge", async t => {
  const ui = await setup(); t.after(() => ui.window.close());
  ui.document.dispatchEvent(new ui.window.KeyboardEvent("keydown", { key: "k", ctrlKey: true }));
  assert.equal(ui.document.getElementById("search-dialog").open, true);
  ui.document.querySelector('[data-route="memory"]').click();
  await until(() => ui.window.location.hash === "#memory");
  assert.equal(ui.document.getElementById("search-dialog").open, false);
  ui.document.getElementById("theme-button").click();
  assert.equal(ui.document.documentElement.dataset.theme, "light");
  assert.equal(ui.window.localStorage.getItem("primitive-theme"), "light");
});

test("timeline details, copy, and older pages use the execution journal", async t => {
  const evidence = { ...record({ action: "memory_delete" }), event_type: "decision" };
  const ui = await setup({ records: [evidence] }); t.after(() => ui.window.close());
  let copied;
  Object.defineProperty(ui.window.navigator, "clipboard", { value: { writeText: async text => { copied = JSON.parse(text); } } });
  ui.sessions.push(session()); ui.window.location.hash = "#session/task-1";
  await until(() => ui.streams.length > 0);
  ui.streams[0].emit({ ...snapshot(ui.sessions[0]), records: [evidence], has_more: true });
  ui.document.querySelector("#timeline .event-detail").click();
  await until(() => ui.document.getElementById("detail-dialog").open);
  assert.ok(ui.requests.some(r => r.url === "/api/evidence/record-1"));
  ui.document.querySelector("[data-copy-record]").click();
  await until(() => copied);
  assert.equal(copied.event_type, "decision");
  assert.equal(ui.requests.filter(r => r.url === "/api/evidence/record-1").length, 2);
  ui.document.getElementById("close-dialog").click();
  ui.document.getElementById("older-events").click();
  await until(() => ui.requests.some(r => r.url === "/api/sessions/task-1/history?limit=60&offset=1"));
  await until(() => ui.document.getElementById("older-events").hidden);
  assert.equal(ui.requests.some(r => r.url.startsWith("/api/memory")), false);
});
