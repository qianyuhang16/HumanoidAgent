const { app, BrowserWindow, Menu, dialog } = require("electron");
const path = require("node:path");
const { randomBytes } = require("node:crypto");
const { options, launchBackend } = require("./backend.cjs");

app.setName("Primitive");
if (process.env.PRIMITIVE_DESKTOP_DATA) app.setPath("userData", process.env.PRIMITIVE_DESKTOP_DATA);
const token = randomBytes(32).toString("hex");
let window, backend, ownedChild, quitting = false, mayQuit = false;

async function backendRequest(endpoint, method = "GET") {
  const response = await fetch(`${backend.url}/api/desktop/${endpoint}`, {
    method, headers: { "X-Primitive-Token": token }, signal: AbortSignal.timeout(30000),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail);
  return result;
}

async function requestExit() {
  if (quitting) return;
  quitting = true;
  try {
    if (backend && backend.child.exitCode === null) {
      const active = await backendRequest("activity");
      if (active.running || active.active_calls) {
        const answer = await dialog.showMessageBox(window, {
          type: "question", title: "任务仍在运行", message: "停止任务并退出 Primitive？",
          detail: "应用会先暂停决策，并等待 Provider 确认动作停止。",
          buttons: ["继续工作", "停止任务并退出"], defaultId: 0, cancelId: 0,
        });
        if (answer.response === 0) return;
      }
      await backendRequest("prepare-close", "POST");
      backend.child.kill("SIGTERM");
    } else if (!backend) {
      ownedChild?.kill("SIGTERM");
    }
    mayQuit = true;
    app.quit();
  } catch (error) {
    await dialog.showMessageBox(window, { type: "warning", title: "暂时无法退出", message: error.message, buttons: ["返回工作台"] });
  } finally { quitting = false; }
}

function menu() {
  const send = command => window?.webContents.send("primitive:command", command);
  const template = [
    ...(process.platform === "darwin" ? [{ role: "appMenu" }] : []),
    { label: "任务", submenu: [
      { label: "新建任务", accelerator: "CmdOrCtrl+N", click: () => send("new-task") },
      { label: "搜索任务", accelerator: "CmdOrCtrl+K", click: () => send("search") },
      { type: "separator" }, { label: "退出 Primitive", accelerator: "CmdOrCtrl+Q", click: requestExit },
    ] },
    { label: "编辑", submenu: [{ role: "undo" }, { role: "redo" }, { type: "separator" }, { role: "cut" }, { role: "copy" }, { role: "paste" }, { role: "selectAll" }] },
    { label: "视图", submenu: [{ role: "reload" }, { role: "resetZoom" }, { role: "zoomIn" }, { role: "zoomOut" }, { type: "separator" }, { role: "togglefullscreen" }, { role: "toggleDevTools" }] },
    { label: "窗口", submenu: [{ role: "minimize" }, { role: "zoom" }] },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

async function start() {
  const root = app.isPackaged ? path.join(process.resourcesPath, "framework") : path.resolve(__dirname, "..");
  const config = options(process.argv, root, app.getPath("userData"));
  window = new BrowserWindow({
    width: 1440, height: 940, minWidth: 850, minHeight: 600,
    title: "Primitive · Agent 工作台", backgroundColor: "#181a1b", show: false,
    webPreferences: { preload: path.join(__dirname, "preload.cjs"), nodeIntegration: false, contextIsolation: true, sandbox: true },
  });
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event, url) => { if (!backend || new URL(url).origin !== backend.url) event.preventDefault(); });
  window.webContents.session.setPermissionRequestHandler((contents, permission, callback) => {
    callback(permission === "clipboard-sanitized-write" && Boolean(backend) && new URL(contents.getURL()).origin === backend.url);
  });
  window.webContents.session.setPermissionCheckHandler((_contents, permission, origin) =>
    permission === "clipboard-sanitized-write" && origin === backend?.url);
  window.on("close", event => { if (!mayQuit) { event.preventDefault(); requestExit(); } });
  window.once("ready-to-show", () => window.show());
  menu();
  await window.loadFile(path.join(__dirname, "loading.html"));
  backend = await launchBackend(config, root, token, child => { ownedChild = child; });
  window.webContents.session.webRequest.onBeforeSendHeaders({ urls: [`${backend.url}/*`] }, (details, callback) => {
    callback({ requestHeaders: { ...details.requestHeaders, "X-Primitive-Token": token } });
  });
  backend.child.on("exit", code => {
    if (!quitting && !mayQuit && window && !window.isDestroyed()) {
      dialog.showMessageBox(window, { type: "error", title: "Runtime 已断开", message: `本地 Runtime 已退出 (${code})。`, detail: "任务记录已保存在工作区。重新启动应用后，未完成动作需要重新确认。", buttons: ["知道了"] });
    }
  });
  await window.loadURL(backend.url);
}

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on("second-instance", () => { if (window) { if (window.isMinimized()) window.restore(); window.focus(); } });
  app.on("before-quit", event => { if (!mayQuit) { event.preventDefault(); requestExit(); } });
  app.whenReady().then(start).catch(error => {
    if (mayQuit) return;
    dialog.showErrorBox("Primitive 启动失败", `${error.message}\n\n请确认 Python 环境已安装 primitive-vla[gui]，或设置 PRIMITIVE_PYTHON 指向正确环境。`);
    ownedChild?.kill("SIGTERM");
    mayQuit = true; app.quit();
  });
}
