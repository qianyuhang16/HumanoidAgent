import argparse
import importlib
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description="Primitive Agent local workbench")
    parser.add_argument("command", choices=["gui", "desktop"])
    parser.add_argument("--workspace", default="workspace/agent")
    parser.add_argument("--factory", help="module:function returning a configured FastAPI app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.command == "desktop":
        root = Path(__file__).resolve().parents[4]
        desktop = root / "desktop"
        executable = desktop / "node_modules" / ".bin" / ("electron.cmd" if os.name == "nt" else "electron")
        if not executable.exists():
            parser.error("桌面运行时尚未安装。请在源码目录执行 npm --prefix desktop ci，再运行 primitive-vla desktop。")
        command = [str(executable), str(desktop), "--workspace", str(Path(args.workspace).resolve())]
        if args.factory:
            command.extend(["--factory", args.factory])
        raise SystemExit(subprocess.call(command, env={**os.environ, "PRIMITIVE_PYTHON": sys.executable}))
    import uvicorn
    from .app import create_app

    if args.factory:
        module, function = args.factory.split(":", 1)
        app = getattr(importlib.import_module(module), function)()
    else:
        app = create_app(workspace=args.workspace)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
