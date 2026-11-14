"use strict";

// A read-only consumer. Robot selection is supplied by the workbench; cameras by the Provider.
window.PrimitiveRobotView = class {
  constructor(root) {
    this.root = root;
    this.robotId = "";
    this.views = [];
    this.visible = true;
    this.collapsed = false;
    this.failed = false;
    this.stream = null;
    this.catalogRequest = null;
    root.classList.add("robot-view");
    root.innerHTML = `
      <header class="robot-view-heading">
        <h3><span class="robot-view-dot"></span>机器人视角</h3>
        <button type="button" class="robot-view-toggle" aria-expanded="true">收起</button>
      </header>
      <div class="robot-view-body">
        <p class="robot-view-robot">尚未选择机器人</p>
        <div class="robot-view-toolbar">
          <select aria-label="机器人相机" disabled><option>未提供视角</option></select>
          <button type="button" class="robot-view-expand" aria-label="放大机器人画面" disabled>放大 ↗</button>
        </div>
        <button type="button" class="robot-view-stage" aria-label="放大机器人画面" disabled>
          <img alt="机器人当前画面" hidden>
          <span class="robot-view-placeholder">选择机器人后查看画面</span>
        </button>
        <div class="robot-view-info"><span class="robot-view-status" role="status">未选择机器人</span><button type="button" class="robot-view-retry" hidden>重新连接</button></div>
        <p class="robot-view-caption">最后采集 · <time>尚未采集</time></p>
      </div>`;
    this.select = root.querySelector("select");
    this.image = root.querySelector("img");
    this.placeholder = root.querySelector(".robot-view-placeholder");
    this.expand = root.querySelector(".robot-view-expand");
    this.stage = root.querySelector(".robot-view-stage");
    this.retry = root.querySelector(".robot-view-retry");
    this.time = root.querySelector("time");
    this.dialog = document.createElement("dialog");
    this.dialog.className = "robot-view-dialog";
    this.dialog.setAttribute("aria-label", "机器人当前画面");
    this.dialog.innerHTML = `<header><div><p>机器人当前画面</p><h2></h2></div><button type="button" aria-label="关闭放大画面">关闭 ×</button></header><img alt="机器人当前画面"><footer></footer>`;
    document.body.append(this.dialog);
    this.dialogImage = this.dialog.querySelector("img");
    this.dialog.querySelector("button").addEventListener("click", () => this.dialog.close());
    const enlarge = () => { if (!this.image.hidden) this.dialog.showModal(); };
    this.stage.addEventListener("click", enlarge);
    this.expand.addEventListener("click", enlarge);
    this.select.addEventListener("change", () => {
      this.failed = false;
      this.stop();
      this.clearFrame();
      this.updateSubscription();
    });
    root.querySelector(".robot-view-toggle").addEventListener("click", event => {
      this.collapsed = !this.collapsed;
      root.querySelector(".robot-view-body").hidden = this.collapsed;
      event.currentTarget.textContent = this.collapsed ? "展开" : "收起";
      event.currentTarget.setAttribute("aria-expanded", String(!this.collapsed));
      this.updateSubscription();
    });
    this.retry.addEventListener("click", () => {
      this.failed = false;
      if (!this.views.length) this.setRobot(this.robotId, this.robotLabel, true);
      else this.updateSubscription();
    });
    this.visibilityChanged = () => this.updateSubscription();
    document.addEventListener("visibilitychange", this.visibilityChanged);
    this.unload = () => this.destroy();
    window.addEventListener("beforeunload", this.unload);
  }

  async setRobot(robotId, label = "", reload = false) {
    if (this.robotId === robotId && !reload) return;
    this.catalogRequest?.abort();
    this.stop();
    this.clearFrame();
    this.robotId = robotId;
    this.robotLabel = label;
    this.views = [];
    this.failed = false;
    this.root.querySelector(".robot-view-robot").textContent = label || robotId || "尚未选择机器人";
    this.select.replaceChildren(new Option("未提供视角", ""));
    this.select.disabled = true;
    if (!robotId) {
      this.setStatus("未选择机器人");
      this.placeholder.textContent = "选择机器人后查看画面";
      return;
    }
    const request = new AbortController();
    this.catalogRequest = request;
    this.setStatus("正在读取视角…");
    this.placeholder.textContent = "正在读取机器人视角";
    try {
      const response = await fetch(`/api/robots/${encodeURIComponent(robotId)}/views`, {signal: request.signal});
      const data = await response.json();
      if (request.signal.aborted) return;
      if (!response.ok) throw new Error(data.detail);
      this.views = data.views;
      if (!this.views.length) {
        this.setStatus("未提供机器人视角");
        this.placeholder.textContent = "接入方尚未提供相机画面";
        return;
      }
      this.select.replaceChildren(...this.views.map(view => new Option(view.label, view.id)));
      this.select.value = this.views.find(view => view.is_default).id;
      this.select.disabled = false;
      this.updateSubscription();
    } catch (error) {
      if (!request.signal.aborted) this.fail(error.message);
    }
  }

  setVisible(visible) {
    this.visible = visible;
    this.updateSubscription();
  }

  updateSubscription() {
    const watching = this.visible && !this.collapsed && document.visibilityState === "visible";
    if (!watching) {
      this.stop();
      if (this.views.length && !this.failed) this.setStatus("预览已暂停");
      if (this.dialog.open) this.dialog.close();
      return;
    }
    if (!this.views.length || this.stream || this.failed) return;
    this.retry.hidden = true;
    this.setStatus("正在连接…");
    this.placeholder.textContent = "等待相机画面";
    const stream = new EventSource(`/api/robots/${encodeURIComponent(this.robotId)}/views/${encodeURIComponent(this.select.value)}/stream`);
    this.stream = stream;
    stream.onopen = () => { if (this.stream === stream) this.setStatus("等待画面"); };
    stream.addEventListener("frame", event => {
      if (this.stream !== stream) return;
      const frame = JSON.parse(event.data);
      const url = `data:${frame.media_type};base64,${frame.image}`;
      this.image.src = this.dialogImage.src = url;
      this.image.hidden = false;
      this.placeholder.hidden = true;
      this.stage.disabled = this.expand.disabled = false;
      const captured = new Date(frame.captured_at * 1000);
      this.time.dateTime = captured.toISOString();
      this.time.textContent = captured.toLocaleTimeString("zh-CN", {hour12: false});
      this.dialog.querySelector("h2").textContent = `${this.robotLabel} · ${this.select.selectedOptions[0].textContent}`;
      this.setStatus("预览中", "live");
    });
    stream.addEventListener("view_error", event => {
      if (this.stream === stream) this.fail(JSON.parse(event.data).message);
    });
    stream.addEventListener("view_closed", () => {
      if (this.stream === stream) this.fail("画面来源已关闭");
    });
    stream.onerror = () => { if (this.stream === stream) this.fail("画面连接中断"); };
  }

  setStatus(message, tone = "") {
    this.root.dataset.state = tone;
    this.root.querySelector(".robot-view-status").textContent = message;
    this.dialog.querySelector("footer").textContent = `${message} · 最后采集 ${this.time.textContent}`;
  }

  fail(message) {
    this.stop();
    this.failed = true;
    this.setStatus(message, "error");
    this.retry.hidden = false;
    this.placeholder.textContent = "暂时无法获取画面";
  }

  stop() {
    this.stream?.close();
    this.stream = null;
  }

  clearFrame() {
    if (this.dialog.open) this.dialog.close();
    this.image.removeAttribute("src");
    this.dialogImage.removeAttribute("src");
    this.image.hidden = true;
    this.placeholder.hidden = false;
    this.stage.disabled = this.expand.disabled = true;
    this.time.textContent = "尚未采集";
    this.time.removeAttribute("datetime");
    this.retry.hidden = true;
  }

  destroy() {
    this.catalogRequest?.abort();
    this.stop();
    document.removeEventListener("visibilitychange", this.visibilityChanged);
    window.removeEventListener("beforeunload", this.unload);
    this.dialog.remove();
  }
};
