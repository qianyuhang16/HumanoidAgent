"""Evaluation adapter; the generic Agent receives no task skills or evaluator feedback."""
import asyncio
import json
import time
import traceback

from primitive_vla.agent.tool_loop import ToolAgent
from primitive_vla.backends.rmbench import RMBenchBackend
from primitive_vla.backends.rmbench_tools import RMBenchTools
from primitive_vla.config.schema import VLMConfig
from primitive_vla.providers.factory import create_client
from primitive_vla.storage.events import EventLog
from pathlib import Path


async def episode(config, directory):
    directory.mkdir(parents=True, exist_ok=False)
    events = (directory/'events.jsonl').open('w')
    log = EventLog(sink=lambda event: (events.write(json.dumps(event,ensure_ascii=False)+'\n'),events.flush()))
    backend = RMBenchBackend(config, directory, log)
    tools = RMBenchTools(backend, directory, log)
    client = create_client(VLMConfig.model_validate(config['vlm']),directory/'media',log=log)
    started = time.monotonic()
    report = {'status':'failed', 'agent_declared_complete':False}
    try:
        await backend.warmup()
        agent = ToolAgent(client, tools, Path(config['planner_prompt']).read_text(), log,
            max_calls=config['tool_agent']['max_calls'], timeout_s=config['task']['timeout_s'])
        report = await agent.run(config['instruction'])
    except asyncio.CancelledError:
        report.update(status='cancelled', reason='用户停止任务', agent_declared_complete=False)
        log.write('task_cancelled', reason='用户停止任务')
    except Exception:
        report['error'] = traceback.format_exc()
    finally:
        async def finalize():
            physical = {}
            try:
                physical = (await tools.observe())['proprioception']
            except Exception:
                pass
            # Commit the claim before the independent simulator can grade it.
            (directory/'agent-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
            grade = None
            try:
                if not tools.locked and tools.active is None:
                    grade = await backend.worker.request('evaluate')
            except Exception as error:
                report['evaluation_error'] = str(error)
            report.update(seed=config['seed'],mode='generic_tools',elapsed_s=time.monotonic()-started,
                physical=physical,terminal_evaluation=grade,physical_success=grade['success'] if grade else None,
                observation_mode='sensors',control_locked=tools.locked,
                perception_calls=sum(e['event']=='perception_grounded' for e in log.events),
                agent_calls=sum(e['event']=='model_response' and e.get('purpose')=='tool_plan' for e in log.events),
                model_calls=sum(e['event']=='vla_chunk_executed' for e in log.events))
            (directory/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
            await asyncio.gather(client.close(),tools.close(),backend.close(),return_exceptions=True)
            events.close()
        cleanup = asyncio.create_task(finalize())
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await cleanup
    print(json.dumps({'run':str(directory),'status':report['status'],'success':report['physical_success'],
                      'physical':report.get('physical',{}),'error':report.get('error')},ensure_ascii=False),flush=True)
    return report
