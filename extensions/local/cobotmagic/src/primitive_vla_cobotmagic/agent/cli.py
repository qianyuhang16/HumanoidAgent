"""General Agent workspace with configurable tools and optional reviewed terminal execution."""
import argparse
import asyncio
import json
from pathlib import Path
import time
from uuid import uuid4
from dotenv import load_dotenv
from primitive_vla.runtime.session import Runtime
from primitive_vla.storage.events import EventLog
from primitive_vla.tools import CallContext
from ..ik.pixel import execute_bundle
from ..perception.client import inference_process, infer
from .config import DEFAULT_CONFIG, agent_config, settings
from .provider import CobotTools
from ..vla.client import close_workspace


async def run_agent(args):
    config=agent_config(args.config,args.arm)
    folder=args.workspace/'runs'/('agent-'+uuid4().hex)
    folder.mkdir(parents=True,exist_ok=True)
    def log_event(event):
        with (folder/'events.jsonl').open('a') as stream:
            stream.write(json.dumps(event,ensure_ascii=False)+'\n')
        if event['event']=='tool_finished':
            name=event['call']['name']
            status=event['result']['status']
            if name=='robot.plan_hover' and status=='ok':
                status='规划通过' if event['result']['data'].get('execution_ready') else '规划未通过'
            print(f"{name} → {status}",flush=True)
    runtime=Runtime(config,args.workspace,EventLog(sink=log_event),config_dir=args.config.resolve().parent)
    try:
        report=await runtime.run(args.task)
        provider=next((r for r in runtime.resources if isinstance(r,CobotTools)),None)
        plan=provider.last_plan if provider else None
        (folder/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(f"任务结果：{report['status']}；{report['reason']}\n记录：{folder}")
        return report,plan
    finally:
        try:await runtime.close()
        finally:await close_workspace(args.workspace)


async def probe_image(args):
    import numpy as np
    from PIL import Image
    media=args.workspace/'media'
    media.mkdir(parents=True,exist_ok=True)
    frame_ref=f'{uuid4().hex}-offline-rgb.npz'
    with Image.open(args.check_image) as image:
        np.savez_compressed(media/frame_ref,rgb=np.asarray(image.convert('RGB')))
    options=settings(args.config)
    process=inference_process(options,args.workspace)
    results={}
    try:
        for op in ('segment','depth'):
            arguments={'frame_ref':frame_ref}
            if op=='segment':arguments['query']=args.query
            results[op]=await infer(process,op,arguments,
                CallContext(uuid4().hex,time.monotonic()+options['inference_timeout_s']))
        result_path=args.workspace/'probe.json'
        result_path.write_text(json.dumps(results,ensure_ascii=False,indent=2))
        print(json.dumps(results,ensure_ascii=False,indent=2))
        print(f'仅已有图片推理，无 ROS 连接：{result_path}')
        return 0 if results['segment']['found'] else 1
    finally:
        await process.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=DEFAULT_CONFIG)
    parser.add_argument('--workspace',type=Path,default=Path('workspace/cobotmagic-agent'))
    parser.add_argument('--arm',choices=['auto','left','right'],default='auto',help='Agent selects by default; optional fixed-arm override for terminal testing')
    parser.add_argument('--task',default='观察当前环境，并告诉我你能做什么。')
    parser.add_argument('--execute',action='store_true',help='after planning, wait for Space and execute through the existing bridge')
    parser.add_argument('--check-image',type=Path,help='test segmentation and depth on an existing image without ROS or an Agent API call')
    parser.add_argument('--query',default='pink bowl')
    parser.add_argument('--env-file',type=Path,default=Path('.env'))
    parser.add_argument('--web',action='store_true',help='start the Agent observation and planning web UI')
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=18086)
    args=parser.parse_args(argv)
    if args.web and (args.execute or args.check_image):
        parser.error('--web cannot be combined with --execute or --check-image')
    if args.check_image and args.execute:
        parser.error('--check-image cannot execute motion')
    load_dotenv(args.env_file,override=False)
    args.workspace=args.workspace.resolve()
    try:
        if args.web:
            from .web import create_app
            import uvicorn
            print(f'Agent 工作台：http://{args.host}:{args.port}；Agent 可调用已配置的真实执行工具。')
            uvicorn.run(create_app(args.workspace/'web',args.config),host=args.host,port=args.port)
            return 0
        if args.check_image:
            return asyncio.run(probe_image(args))
        report,plan=asyncio.run(run_agent(args))
        if report['status']!='completed':
            return 1
        if not plan:
            if any(r.get('tool')=='robot.execute' and r.get('data',{}).get('arrival_verified') for r in report.get('results',[])):
                return 0
            if args.execute:
                print('本次任务没有可执行的运动计划。')
                return 1
            return 0
        print(f"目标 {plan['arm']} 臂 / XYZ {plan['target_xyz_m']} m\n{plan['command']}")
        if args.execute:
            return execute_bundle(plan['bundle'],execute=True)
        print('计划未执行；需要运动时可使用上面的终端命令。')
        return 0
    except KeyboardInterrupt:
        return 130
    except (ValueError,RuntimeError,OSError,KeyError) as error:
        print(f'未完成：{error}')
        return 1


if __name__=='__main__':
    raise SystemExit(main())
