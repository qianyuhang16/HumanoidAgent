const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { mkdtemp, rm } = require("node:fs/promises");
const { tmpdir } = require("node:os");
const { once } = require("node:events");
const { options, launchBackend } = require("../backend.cjs");

test("desktop configuration keeps arguments as data, including spaces", () => {
  const config = options(["--workspace", "/tmp/my robot workspace", "--factory", "robot_app:build"], "/repo", "/user/data", { PRIMITIVE_PYTHON: "/env/bin/python" });
  assert.equal(config.workspace, "/tmp/my robot workspace");
  assert.equal(config.factory, "robot_app:build");
  assert.equal(config.python, "/env/bin/python");
  assert.throws(() => options(["--factory"], "/repo", "/user/data", {}), /requires a value/);
});

test("launcher starts an authenticated real Python workspace and shuts it down", { skip: !process.env.PRIMITIVE_PYTHON, timeout: 30000 }, async () => {
  const workspace = await mkdtemp(path.join(tmpdir(), "primitive-desktop-test-"));
  const root = process.env.PRIMITIVE_FRAMEWORK_ROOT || path.resolve(__dirname, "../..");
  let backend;
  try {
    backend = await launchBackend({ python: process.env.PRIMITIVE_PYTHON, workspace }, root, "integration-token");
    assert.match(backend.url, /^http:\/\/127\.0\.0\.1:\d+$/);
    assert.equal((await fetch(`${backend.url}/api/status`)).status, 401);
    const home = await fetch(backend.url);
    assert.equal(home.status, 200);
    assert.match(await home.text(), /primitive/);
    assert.equal((await fetch(`${backend.url}/static/app.js`)).status, 200);
    const headers = { "X-Primitive-Token": "integration-token", "Content-Type": "application/json" };
    const status = await (await fetch(`${backend.url}/api/status`, { headers })).json();
    assert.equal(status.connected, false);
    assert.equal(status.workspace, workspace);
    const draft = await fetch(`${backend.url}/api/sessions`, { method: "POST", headers, body: JSON.stringify({ goal: "Review the scene" }) });
    assert.equal(draft.status, 201);
    assert.equal((await draft.json()).status, "draft");
    const closing = await fetch(`${backend.url}/api/desktop/prepare-close`, { method: "POST", headers });
    assert.equal(closing.status, 200);
  } finally {
    if (backend) { const exited = once(backend.child, "exit"); backend.child.kill("SIGTERM"); await exited; }
    await rm(workspace, { recursive: true, force: true });
  }
});
