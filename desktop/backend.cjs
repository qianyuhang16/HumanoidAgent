const { spawn } = require("node:child_process");
const { existsSync } = require("node:fs");
const path = require("node:path");
const { setTimeout: delay } = require("node:timers/promises");

function options(argv, root, userData, env = process.env) {
  const argument = name => {
    const index = argv.indexOf(name);
    if (index === -1) return undefined;
    if (!argv[index + 1] || argv[index + 1].startsWith("--")) throw new Error(`${name} requires a value`);
    return argv[index + 1];
  };
  const venv = path.join(root, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
  return {
    python: env.PRIMITIVE_PYTHON || (existsSync(venv) ? venv : process.platform === "win32" ? "python" : "python3"),
    workspace: path.resolve(argument("--workspace") || path.join(userData, "workspace")),
    factory: argument("--factory"),
  };
}

async function launchBackend(config, root, token, onSpawn = () => {}) {
  const args = ["-u", "-m", "primitive_vla.interfaces.web.server", "--workspace", config.workspace];
  if (config.factory) args.push("--factory", config.factory);
  const child = spawn(config.python, args, {
    cwd: root, windowsHide: true, stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, PRIMITIVE_DESKTOP_TOKEN: token,
      PYTHONPATH: [path.join(root, "src"), process.env.PYTHONPATH].filter(Boolean).join(path.delimiter) },
  });
  onSpawn(child);
  let logs = "", buffer = "";
  child.stderr.on("data", chunk => { logs = (logs + chunk).slice(-6000); });
  try {
    const url = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`Runtime 启动超时。\n${logs}`)), 20000);
      const finish = (fn, value) => { clearTimeout(timer); fn(value); };
      child.once("error", error => finish(reject, error));
      child.once("exit", code => finish(reject, new Error(`Python Runtime 已退出 (${code})。\n${logs}`)));
      child.stdout.on("data", chunk => {
        buffer += chunk;
        const lines = buffer.split("\n"); buffer = lines.pop();
        for (const line of lines) {
          if (line.startsWith("PRIMITIVE_LISTENING ")) finish(resolve, line.slice(20).trim());
        }
      });
    });
    const address = new URL(url);
    if (address.protocol !== "http:" || address.hostname !== "127.0.0.1") throw new Error("Runtime must use a local loopback address");
    for (let attempt = 0; attempt < 100; attempt++) {
      if (child.exitCode !== null) throw new Error(`Runtime 已退出。\n${logs}`);
      try {
        const response = await fetch(`${url}/api/status`, { headers: { "X-Primitive-Token": token }, signal: AbortSignal.timeout(1000) });
        if (response.ok) return { child, url };
      } catch (error) {
        if (attempt === 99) throw new Error(`无法连接本地 Runtime。\n${logs}`, { cause: error });
      }
      await delay(100);
    }
    throw new Error(`Runtime 未通过就绪检查。\n${logs}`);
  } catch (error) {
    child.kill("SIGTERM");
    throw error;
  }
}

module.exports = { options, launchBackend };
