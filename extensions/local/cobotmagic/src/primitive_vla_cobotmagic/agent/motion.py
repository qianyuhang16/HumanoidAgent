"""Execute an owned, frozen plan through the existing bridge; no shell dispatch."""
import json
from pathlib import Path
import threading

from ..ik.execution import MotionBridge, execute_plan, load_setup, reviewed_plan

DURATION_S=3.0


class MotionRun:
    def __init__(self, folder):
        self.folder=Path(folder)
        self.cancelled=threading.Event()
        self.sent=False
        self.completed=False
        self.task=None

    @property
    def control_released(self):
        # The existing bridge has no acknowledged physical stop operation.
        return self.completed or not self.sent

    def run(self):
        bridge=None
        trace_path=self.folder/'agent-execution.trace.jsonl'
        result_path=self.folder/'agent-execution.result.json'
        try:
            config,chain=load_setup(self.folder/'hover.yaml')
            saved=json.loads((self.folder/'plan.json').read_text())
            if abs(config['move_duration_s']-DURATION_S)>1e-9 or abs(saved['nominal_duration_s']-DURATION_S)>1e-9:
                raise ValueError('Agent execution requires a newly generated 3-second plan')
            if self.cancelled.is_set():
                raise RuntimeError('Motion cancelled before dispatch')
            bridge=MotionBridge(config,allow_motion=True)
            positions,_=bridge.observation()
            plan=reviewed_plan(config,chain,saved,positions)
            with trace_path.open('w') as stream:
                def record(event):
                    if event['event']=='command_send':self.sent=True
                    if event['event']=='completed':self.completed=True
                    stream.write(json.dumps(event,ensure_ascii=False,allow_nan=False)+'\n')
                    stream.flush()
                measured=execute_plan(bridge,config,chain,plan,on_event=record,cancel_event=self.cancelled)
            result={'executed':True,'arrival_verified':True,'nominal_duration_s':DURATION_S,
                    'arm':config['arm'],**measured,'trace':str(trace_path)}
            result_path.write_text(json.dumps(result,ensure_ascii=False,indent=2))
            return result
        except BaseException as error:
            result_path.write_text(json.dumps({'executed':False,'arrival_verified':False,
                'commands_sent':self.sent,'control_released':self.control_released,'error':str(error)},ensure_ascii=False,indent=2))
            raise
        finally:
            if bridge is not None:bridge.close()
