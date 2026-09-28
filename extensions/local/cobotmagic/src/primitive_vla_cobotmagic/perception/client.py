"""Launch inference in the existing environment without installing another one."""
import os
from pathlib import Path
from time import monotonic, time
from uuid import uuid4
import primitive_vla
from primitive_vla.runtime.process import JsonProcess


def inference_process(options, workspace):
    workspace=Path(workspace)
    paths=[str(Path(__file__).resolve().parents[2]),
           str(Path(primitive_vla.__file__).resolve().parents[1])]
    return JsonProcess(options['python_executable'], 'primitive_vla_cobotmagic.perception.worker', [],
        workspace/'runs'/f'inference-{uuid4().hex}.log', timeout=options['inference_timeout_s'],
        startup_timeout=60, close_timeout=5,
        init_payload={'options':options, 'media':str(workspace/'media')},
        env={'PYTHONPATH':os.pathsep.join(paths), 'HF_HUB_OFFLINE':'1',
             'TRANSFORMERS_OFFLINE':'1','TOKENIZERS_PARALLELISM':'false'})


async def infer(process, operation, arguments, context):
    context.check_active()
    remaining=max(.001,context.deadline-monotonic())
    result=await process.request(operation,arguments=arguments,call_id=context.call_id,
        deadline=time()+remaining,timeout=remaining)
    context.check_active()
    return result
