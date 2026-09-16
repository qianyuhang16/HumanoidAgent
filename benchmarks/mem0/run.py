"""Small paired Agent + Mem-0 evaluation; no official planner or scripted task rollout."""

import argparse
import asyncio
import json
from pathlib import Path
import shutil
import time
import traceback

from primitive_vla.agent.loop import Agent
from primitive_vla.agent.planner import StructuredPlanner
from primitive_vla.backends.rmbench import RMBenchBackend
from primitive_vla.config.schema import VLMConfig
from primitive_vla.domain.models import Goal, SceneObject, Task, Truth
from primitive_vla.domain.skills import Registry
from primitive_vla.domain.state import State
from primitive_vla.execution.executive import Executive
from primitive_vla.providers.factory import create_client
from primitive_vla.skills.loader import load_skills
from primitive_vla.storage.events import EventLog


async def episode(config, directory):
    directory.mkdir(parents=True, exist_ok=False)
    events = (directory/'events.jsonl').open('w')
    log = EventLog(sink=lambda event: (events.write(json.dumps(event, ensure_ascii=False)+'\n'), events.flush()))
    backend = RMBenchBackend(config, directory, log)
    provider = VLMConfig.model_validate(config['vlm'])
    client = create_client(provider, directory/'media', log=log)
    state = State({name: SceneObject(name, kind, name) for names, kind in [
        (('left','middle','right'),'cover_position'), (('red','green','blue'),'block_color')]
        for name in names})
    specs = load_skills(['builtin:rmbench'])
    executive = Executive(Registry(specs), state, {'rmbench': backend}, log, poll_s=1., stop_timeout_s=180.)
    goals = []
    for index, predicate in enumerate(('covered:left','covered:middle','covered:right',
                                       'uncovered:red','uncovered:green','uncovered:blue')):
        goals.append(Goal(f'stage{index+1}', {predicate: Truth.TRUE}, (f'stage{index}',) if index else ()))
    task = Task(config['instruction'], goals, max_steps=8, timeout_s=2400, max_observations=1)
    async def choose(context):
        prompt = ('Choose the next legal skill. Use the initial_memory image to remember which color is at '
                  'each table position. Do not infer the color-position map from the current covered image. '
                  'Return {"action":"skill","index":integer,"reason":"brief evidence basis"}, '
                  'or {"action":"stop","reason":"..."}.')
        reply = await client.complete('plan', prompt, context, state.observation.images)
        return reply
    started = time.monotonic()
    report = {'status': 'failed'}
    try:
        state.update(await backend.observe())
        await backend.warmup()
        report = await Agent(StructuredPlanner(choose), executive, backend).run(task)
    except Exception:
        report['error'] = traceback.format_exc()
    finally:
        try:
            state.update(await backend.observe())
        except Exception:
            pass
        physical = state.observation.proprioception
        report.update(seed=config['seed'], mode=config['mode'], elapsed_s=time.monotonic()-started,
            physical=physical, physical_success=state.values().get('benchmark:success') == Truth.TRUE,
            agent_calls=sum(e['event']=='model_response' and e.get('purpose')=='plan' for e in log.events),
            model_calls=sum(e['event']=='vla_chunk_executed' for e in log.events),
            control_locked=executive.control.lease is not None)
        (directory/'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
        await asyncio.gather(client.close(), backend.close(), return_exceptions=True)
        events.close()
    print(json.dumps({'run':str(directory), 'status':report['status'], 'success':report['physical_success'],
                      'physical':physical, 'error':report.get('error')},ensure_ascii=False), flush=True)
    return report


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=Path('benchmarks/mem0/config.json'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seeds', type=int, nargs='+', default=[100000,100001])
    parser.add_argument('--modes', nargs='+', choices=['vla','ik_approach'], default=['vla','ik_approach'])
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    for key in ('python', 'repo', 'checkpoint', 'backbone', 'norm_stats'):
        config[key] = str(Path(config[key]).resolve())
    args.output.mkdir(parents=True, exist_ok=True)
    shutil.copy(Path(__file__).with_name('report.html'), args.output/'index.html')
    (args.output/'runs.json').write_text(json.dumps([
        f'{mode}-{seed}' for seed in args.seeds for mode in args.modes]))
    results = []
    for seed in args.seeds:
        for mode in args.modes:
            results.append(await episode(dict(config, seed=seed, mode=mode),
                args.output.resolve()/f'{mode}-{seed}'))
            (args.output/'summary.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')


if __name__ == '__main__':
    asyncio.run(main())
