"""Small paired Agent + Mem-0 evaluation; no official planner or scripted task rollout."""

import argparse
import hashlib
import asyncio
import json
from pathlib import Path
import shutil
import time
import traceback
import subprocess
import sys

from primitive_vla.agent.loop import Agent
from primitive_vla.agent.planner import StructuredPlanner
from primitive_vla.backends.rmbench import RMBenchBackend
from primitive_vla.backends.rmbench_visual import VisualRMBenchBackend
from primitive_vla.plugins.grounded_sam.backend import PerceptionBackend
from primitive_vla.config.schema import VLMConfig
from primitive_vla.domain.models import Goal, SceneObject, Task, Truth
from primitive_vla.domain.skills import Registry
from primitive_vla.domain.state import State
from primitive_vla.execution.executive import Executive
from primitive_vla.providers.factory import create_client
from primitive_vla.skills.loader import load_skills
from primitive_vla.storage.events import EventLog


async def episode(config, directory):
    if config.get("agent_interface") == "generic_tools":
        from generic_tools.episode import episode as generic_episode
        return await generic_episode(config, directory)
    directory.mkdir(parents=True, exist_ok=False)
    events = (directory/'events.jsonl').open('w')
    log = EventLog(sink=lambda event: (events.write(json.dumps(event, ensure_ascii=False)+'\n'), events.flush()))
    sensors_only = config.get('observation_mode') == 'sensors'
    backend = (VisualRMBenchBackend if sensors_only else RMBenchBackend)(config, directory, log)
    provider = VLMConfig.model_validate(config['vlm'])
    client = create_client(provider, directory/'media', log=log)
    if config.get('agent_supervision', False):
        async def supervise(context, images):
            return await client.complete('handoff',
                Path(config['handoff_prompt']).read_text(),
                context, images)
        backend.supervisor = supervise
    objects = config.get('objects', {'cover_position': ['left','middle','right'],
                                    'block_color': ['red','green','blue']})
    state = State({name: SceneObject(name, kind, name) for kind, names in objects.items() for name in names})
    specs = load_skills(config.get('skill_sources', ['builtin:rmbench/cover', 'builtin:rmbench/uncover']))
    backends = {'rmbench': backend}
    if sensors_only:
        backends['grounded_sam'] = PerceptionBackend(config['perception'], backend, client, directory, log)
    executive = Executive(Registry(specs), state, backends, log, poll_s=1., stop_timeout_s=180.)
    goals = []
    predicates = config.get('goal_predicates', ['covered:left','covered:middle','covered:right',
                                               'uncovered:red','uncovered:green','uncovered:blue'])
    for index, predicate in enumerate(predicates):
        goals.append(Goal(f'stage{index+1}', {predicate: Truth.TRUE}, (f'stage{index}',) if index else ()))
    task = Task(config['instruction'], goals, **config['task'])
    async def choose(context):
        prompt = ('Choose the next legal skill. Use the initial_memory image to remember which color is at '
                  'each table position. Do not infer the color-position map from the current covered image. '
                  'Return {"action":"skill","index":integer,"reason":"brief evidence basis"}, '
                  'or {"action":"stop","reason":"..."}.')
        if config.get('planner_prompt'):
            prompt = Path(config['planner_prompt']).read_text()
        if sensors_only:
            context['perception'] = backend.perception_evidence
        reply = await client.complete('plan', prompt, context, state.observation.images)
        return reply
    started = time.monotonic()
    report = {'status': 'failed'}
    try:
        state.update(await backend.observe())
        await backend.warmup()
        report = await Agent(StructuredPlanner(choose, history_limit=config.get('history_limit', 4),
            adaptive_memory=config.get('adaptive_memory', False)), executive, backend).run(task)
    except Exception:
        report['error'] = traceback.format_exc()
    finally:
        report['final_observation_available'] = False
        try:
            state.update(await backend.observe())
            report['final_observation_available'] = True
        except Exception:
            pass
        physical = state.observation.proprioception
        if sensors_only:
            # Persist the Agent's decision before exposing the held-out evaluator answer.
            (directory/'agent-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
            report['terminal_evaluation'] = None
            try:
                report['terminal_evaluation'] = await backend.worker.request('evaluate')
            except Exception as error:
                report['evaluation_error'] = str(error)
        report.update(seed=config['seed'], mode=config['mode'], elapsed_s=time.monotonic()-started,
            physical=physical, physical_success=(report['terminal_evaluation']['success']
                if sensors_only and report.get('terminal_evaluation') is not None else
                None if sensors_only else state.values().get('benchmark:success') == Truth.TRUE),
            observation_mode=config.get('observation_mode', 'oracle'),
            agent_declared_complete=state.values().get('agent:complete') == Truth.TRUE,
            perception_calls=sum(e['event']=='perception_assessed' for e in log.events),
            agent_calls=sum(e['event']=='model_response' and e.get('purpose')=='plan' for e in log.events),
            handoff_calls=sum(e['event']=='model_response' and e.get('purpose')=='handoff' for e in log.events),
            model_calls=sum(e['event']=='vla_chunk_executed' for e in log.events),
            control_locked=executive.control.lease is not None)
        (directory/'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
        await asyncio.gather(client.close(), *(b.close() for b in backends.values()), return_exceptions=True)
        events.close()
    print(json.dumps({'run':str(directory), 'status':report['status'], 'success':report['physical_success'],
                      'physical':physical, 'error':report.get('error')},ensure_ascii=False), flush=True)
    return report


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=Path('benchmarks/mem0/config.json'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--tier', choices=['development', 'test'], default='development')
    parser.add_argument('--seeds', type=int, nargs='+', default=[100000,100001])
    parser.add_argument('--modes', nargs='+', choices=['vla','ik_approach'], default=['vla','ik_approach'])
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    for key in ('python', 'repo', 'checkpoint', 'backbone', 'norm_stats', 'handoff_prompt', 'planner_prompt'):
        if key not in config:
            continue
        # Resolving the Python symlink bypasses the venv and loses its packages.
        config[key] = str(Path(config[key]).absolute())
    if config.get('observation_mode') == 'sensors':
        for key in ('detector', 'segmenter'):
            config['perception'][key] = str(Path(config['perception'][key]).absolute())
    args.output.mkdir(parents=True, exist_ok=True)
    report_template = Path(__file__).with_name('report.html')
    if config.get('observation_mode') == 'sensors':
        report_template = Path(__file__).parent/'battery_visual'/'report.html'
    shutil.copy(report_template, args.output/'index.html')
    (args.output/'runs.json').write_text(json.dumps([
        f'{mode}-{seed}' for seed in args.seeds for mode in args.modes]))
    (args.output/'manifest.json').write_text(json.dumps({
        'command': [sys.executable, *sys.argv], 'tier': args.tier, 'seeds': args.seeds,
        'modes': args.modes, 'workers': args.workers, 'config': config,
        'revision': subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        'git_status': subprocess.check_output(['git','status','--short'], text=True),
        'source_sha256': {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for root in (Path('src/primitive_vla'), Path('benchmarks/mem0/battery_visual'), Path('benchmarks/mem0/generic_tools'))
            for p in sorted(root.rglob('*')) if p.suffix in ('.py', '.md', '.json', '.html')},
    }, ensure_ascii=False, indent=2)+'\n')
    results = []
    semaphore = asyncio.Semaphore(args.workers)
    async def run_one(seed, mode):
        async with semaphore:
            report = await episode(dict(config, seed=seed, mode=mode),
                args.output.resolve()/f'{mode}-{seed}')
            results.append(report)
            (args.output/'summary.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
    await asyncio.gather(*(run_one(seed, mode) for seed in args.seeds for mode in args.modes))



if __name__ == '__main__':
    asyncio.run(main())
