import { cp, mkdir, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(fileURLToPath(import.meta.url));
if (process.platform !== "linux") throw new Error("This packaging command currently targets Linux; npm start also supports macOS and Windows.");
const output = path.join(root, "dist", `Primitive-linux-${process.arch}`);
await mkdir(output, { recursive: true });
await cp(path.join(root, "node_modules/electron/dist"), output, { recursive: true });
await rename(path.join(output, "electron"), path.join(output, "Primitive"));
const app = path.join(output, "resources/app");
await mkdir(app, { recursive: true });
for (const name of ["main.cjs", "backend.cjs", "preload.cjs", "loading.html", "loading.css"])
  await cp(path.join(root, name), path.join(app, name));
await writeFile(path.join(app, "package.json"), JSON.stringify({ name: "primitive-agent-desktop", version: "1.0.0", main: "main.cjs" }));
await cp(path.join(root, "../src/primitive_vla"), path.join(output, "resources/framework/src/primitive_vla"), {
  recursive: true, filter: source => !source.includes("__pycache__"),
});
for (const name of ["pyproject.toml", "README.md"])
  await cp(path.join(root, "..", name), path.join(output, "resources/framework", name));
await writeFile(path.join(output, "README.txt"), "Primitive desktop\n\npython3 -m venv .venv\n.venv/bin/python -m pip install './resources/framework[gui]'\n./Launch-Primitive.sh\n\nYou can also set PRIMITIVE_PYTHON to an existing Python environment.\nNo model or robot provider is bundled.\n");
await writeFile(path.join(output, "Launch-Primitive.sh"), '#!/bin/sh\nAPP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\nif [ -z "${PRIMITIVE_PYTHON:-}" ] && [ -x "$APP_DIR/.venv/bin/python" ]; then\n  export PRIMITIVE_PYTHON="$APP_DIR/.venv/bin/python"\nfi\nexec "$APP_DIR/Primitive" "$@"\n', { mode: 0o755 });
console.log(output);
