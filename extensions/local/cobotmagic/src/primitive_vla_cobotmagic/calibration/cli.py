"""Command-line entry for shared camera, hand-eye and TCP calibration."""
import argparse
import json
from pathlib import Path

import cv2

from .core import DEFAULT_CONFIG, Board, Collector, Dataset, read_config, solve_session


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    web = commands.add_parser("web", help="Unified camera / left / right calibration workspace")
    web.add_argument("--workspace", type=Path, default=Path("workspace/cobotmagic-calibration"))
    web.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=18084)
    board_cmd = commands.add_parser("board", help="Export a printable A4 ChArUco SVG")
    board_cmd.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    board_cmd.add_argument("--output", type=Path, default=Path("workspace/cobotmagic-calibration/board"))
    capture = commands.add_parser("capture", help="Read-only browser keyboard capture")
    capture.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    capture.add_argument("--arm", choices=("left", "right"), required=True)
    capture.add_argument("--phase", choices=("intrinsics", "handeye"), required=True)
    capture.add_argument("--session", type=Path, required=True)
    capture.add_argument("--intrinsics", type=Path)
    capture.add_argument("--host", default="127.0.0.1", help="Web server bind address")
    capture.add_argument("--port", type=int, default=18084)
    solve = commands.add_parser("solve", help="Offline solve, with independent validation")
    solve.add_argument("--session", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "web":
            from .workspace import CalibrationWorkspace
            from .web import create_workspace_app
            import uvicorn
            workspace = CalibrationWorkspace(args.workspace, args.config)
            print(f"双臂标定工作台：http://{args.host}:{args.port}；工作区：{workspace.root}")
            uvicorn.run(create_workspace_app(workspace), host=args.host, port=args.port, access_log=False)
        elif args.command == "board":
            Board(read_config(args.config)["board"]).export(args.output)
            print(args.output / "board-a4.svg")
        elif args.command == "solve":
            manifest = json.loads((args.session / "manifest.json").read_text())
            if manifest.get("phase") == "tcp":
                from .tcp import solve_tcp_session
                result = solve_tcp_session(args.session)
            else:
                result = solve_session(args.session)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["accepted"] else 2
        else:
            from .web import create_app
            import uvicorn
            intrinsics = json.loads(args.intrinsics.read_text()) if args.intrinsics else None
            dataset = Dataset(args.session, read_config(args.config, arm=args.arm), args.phase, intrinsics)
            arm_name = "左臂" if dataset.config["arm"] == "left" else "右臂"
            print(f"当前：{arm_name} / {dataset.config['camera']} 相机 / {args.phase}；保存到 {dataset.folder}")
            print(f"只读采集：http://{args.host}:{args.port}；空格保存训练样本，V 保存验证样本。")
            uvicorn.run(create_app(Collector(dataset)), host=args.host, port=args.port, access_log=False)
        return 0
    except (ValueError, KeyError, OSError, cv2.error) as error:
        parser.exit(1, f"标定未完成：{error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
