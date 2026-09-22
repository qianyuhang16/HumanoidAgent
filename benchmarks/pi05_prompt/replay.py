"""Publish completed evaluation videos and concise evidence to a local static page."""
import html
import json
import shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'workspace/pi05/evaluation'
SITE=OUT/'replay'
SITE.mkdir(exist_ok=True)
labels={'official_global':'RMBench 原始整句（跨模型参照）','released_left':'发布文本：左电池放中间','paraphrase_left':'同义改写：左电池移到中央',
        'released_right':'切换目标：右电池放中间','grasp_hold_left':'改变目的：抓左电池并保持'}
cards=[]
for path in sorted((OUT/'direct').glob('3*.json')):
    run=json.loads(path.read_text()); scene=Path(run['scene']); video=scene/'media/rollout.mp4'
    # A later scene means the previous video writer has closed its MP4 container.
    next_scene=scene.parent/f'scene-{int(scene.name.split("-")[1])+1:03d}'
    if not next_scene.exists(): continue
    name=path.stem+'.mp4';shutil.copy2(video,SITE/name)
    command=run['command'];diag=command['diagnostics']
    description=f"左电池 XY 对槽：{diag['batteries']['left']['in_slot_xy']}；右电池：{diag['batteries']['right']['in_slot_xy']}；仪表通电：{diag['instrument_on']}"
    cards.append(f'<article><h2>{labels[run["condition"]]}</h2><p>种子 {run["seed"]} · {command["actions"]} 步</p><blockquote>{html.escape(command["prompt"])}</blockquote><video controls preload="metadata" src="{name}"></video><p>{description}</p><a download href="{name}">下载视频</a></article>')
for path in sorted((OUT/'agent').glob('*/summary.json')):
    run=json.loads(path.read_text());video=path.parent/'media/rollout.mp4'
    if not video.exists():continue
    name='agent-'+path.parent.name+'.mp4';shutil.copy2(video,SITE/name)
    trace=[]
    for line in (path.parent/'events.jsonl').read_text().splitlines():
        event=json.loads(line)
        if event['event']=='decision':trace.append({'call':event['call'],'reason':event['reason']})
    cards.append(f'<article><h2>通用 Agent · 种子 {run["seed"]}</h2><p>独立最终判定：{run["physical_success"]}；Agent 声称完成：{run["agent_declared_complete"]}</p><video controls preload="metadata" src="{name}"></video><details><summary>查看规划与工具调用摘要</summary><pre>{html.escape(json.dumps(trace,ensure_ascii=False,indent=2))}</pre></details><a download href="{name}">下载视频</a></article>')
page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>π0.5 电池操作回放</title><style>body{font:16px/1.6 system-ui;background:#101821;color:#e9eef5;margin:0;padding:32px}header,main{max-width:1250px;margin:auto}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:24px}article{background:#1b2836;padding:22px;border-radius:14px}h1{font-size:32px}h2{font-size:20px}video{width:100%;background:#000}a{color:#7ee0c5}blockquote{margin:12px 0;min-height:80px;color:#b9cbdc}pre{white-space:pre-wrap;font-size:13px}header p{color:#b9cbdc}</style><header><h1>π0.5 · 电池操作回放</h1><p>相同种子、每组 300 步。每个动作采一帧，10 fps 回放，不代表真实耗时。XY 对槽并不等于完成插入；抓持与放置效果请结合画面。</p><p>这里的发布文本来自所用社区 π0.5 训练标签。小样本结果仅用于检查当前接入，非官方 benchmark 复现。刷新页面查看新完成的视频。</p><p><a href="http://127.0.0.1:8087/">打开实时 VLA 实验台</a></p></header><main>'''+''.join(cards)+'</main></html>'
(SITE/'index.html').write_text(page)
print(f'Published {len(cards)} videos to {SITE}',flush=True)
