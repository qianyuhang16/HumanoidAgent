"""Capture camera images through the same tool used by the Agent, without an LLM."""

import argparse
import asyncio
import html
import json
from pathlib import Path

from primitive_vla.config.loader import load_config
from primitive_vla.runtime.composition import load_adapter
from primitive_vla.storage.events import EventLog
from primitive_vla.tools import PluginContext, ToolCall
from primitive_vla.tools.runtime import ToolRuntime


async def capture(args):
    config = load_config(args.config)
    spec = next(spec for spec in config.tool_providers if spec.adapter == "primitive_vla_cobotmagic.provider:CameraTools")
    workspace = args.workspace.resolve()
    log = EventLog(workspace / "events.jsonl")
    provider = load_adapter(spec.adapter)(spec.options, PluginContext(workspace, args.config.resolve().parent, log))
    runtime = ToolRuntime(provider.tools, log=log)
    try:
        arguments = {"cameras": args.cameras} if args.cameras else {}
        result = await runtime.invoke(ToolCall(name="camera.observe", arguments=arguments))
        (workspace / "capture.json").write_text(json.dumps(result.model_dump(), ensure_ascii=False, indent=2) + "\n")
        labels = {"front": "前相机", "left": "左相机", "right": "右相机"}
        cards = []
        for camera, ref in result.images.items():
            metadata = result.data["cameras"][camera]
            cards.append(f'<figure><figcaption>{labels[camera]} · {metadata["width"]} × {metadata["height"]}</figcaption>'
                         f'<a href="media/{html.escape(ref, quote=True)}"><img src="media/{html.escape(ref, quote=True)}"></a></figure>')
        page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">' \
            '<title>Cobot Magic 相机</title><style>body{font:16px/1.6 system-ui;background:#142020;color:#e4eeeb;' \
            'max-width:1280px;margin:32px auto;padding:0 16px}figure{margin:20px 0}img{width:100%;border-radius:8px}</style>' \
            '<h1>Cobot Magic 相机快照</h1><p>这是一次采集的静态图像；三路相机不是同步曝光。</p>' \
            + (''.join(cards) if cards else f'<p>采集失败：{html.escape(result.text)}</p>') + '</html>'
        (workspace / "capture.html").write_text(page, encoding="utf-8")
        print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))
        print(f"Preview: {workspace / 'capture.html'}")
        return result.status == "ok"
    finally:
        await runtime.close()
        await provider.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, default=Path("workspace/cobotmagic-camera"))
    parser.add_argument("--cameras", nargs="+", choices=("front", "left", "right"))
    args = parser.parse_args()
    raise SystemExit(0 if asyncio.run(capture(args)) else 1)


if __name__ == "__main__":
    main()
