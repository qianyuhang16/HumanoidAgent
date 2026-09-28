"""Agent tools share one camera snapshot from perception through calibrated IK."""
import asyncio
import io
from pathlib import Path
from uuid import uuid4
import numpy as np
from PIL import Image
from primitive_vla.tools import FunctionTool, ToolDefinition, ToolResult
from ..ik.pixel import PixelIK, PixelTarget
from ..perception.client import inference_process, infer
from .motion import MotionRun, DURATION_S
from .reset import ResetRun
from .arm_state import ArmState
from .arm_context import arm_context
from ..vla.config import ResetSettings
from ..vla.client import client_for


def schema(*, optional=(), **properties):
    return {'type':'object','properties':properties,
            'required':[name for name in properties if name not in optional],'additionalProperties':False}


class CobotTools:
    def __init__(self, options, context):
        self.options=options
        self.context=context
        self.arm_state=ArmState(context.workspace)
        self.reset_settings=ResetSettings.model_validate(options.get('reset',{})).model_dump()
        self.preparations={}
        self.vla_calls=set()
        self.arm=options.get('arm','auto')
        if self.arm not in ('auto','left','right'):
            raise ValueError('arm must be auto/left/right')
        self.arms=['left','right'] if self.arm=='auto' else [self.arm]
        self.media=Path(context.workspace)/'media'
        self.media.mkdir(parents=True,exist_ok=True)
        self.ik=PixelIK(options['ik_config'])
        self.ik.options.motion.move_duration_s=DURATION_S
        self.ik.settings['motion']['move_duration_s']=DURATION_S
        self.process=inference_process(options,context.workspace)
        self.lock=asyncio.Lock()
        self.observation=None
        self.observation_call_id=None
        self.detections={}
        self.last_plan=None
        self.motion_runs={}
        self.vla=None
        if options.get('vla'):
            self.vla=client_for(options['vla'],context.workspace,self.connection())
        string={'type':'string','minLength':1}
        specs=[
            ('camera.observe',self.observe,schema(), 'Read a fresh RGB image from the fixed front camera. Returns image, camera placement and observation_id. Refresh replaces previous detections and plans. No motion.'),
            ('perception.segment',self.segment,schema(optional=('observation_id',),query=string,observation_id=string), 'Find an object with an English query, e.g. pink bowl, using GroundingDINO + SAM. Omit observation_id to use the current image; if none exists, capture one automatically. Returns detection_id, overlay and arm_context with calibrated distances and nearer-arm preference. Different queries on one image retain their detections. Actual motion or a new image invalidates old detections; a failed motion precheck does not. Inspect the overlay before approaching.'),
            ('perception.depth',self.depth,schema(optional=('observation_id',),observation_id=string), 'Run Depth Anything on the current RGB image (or capture one if missing). observation_id is optional. Prediction only, not measured depth or collision clearance. Does not alter IK geometry.'),
            ('robot.plan_hover',self.plan,schema(detection_id=string,arm={'type':'string','enum':self.arms}), 'Plan a calibrated 3-second hover above a detected object with the specified arm. Prefer the nearer same-side arm using perception arm_context unless the task or scene gives a reason otherwise. Local IK failure is not proof of unreachability; do not equate another arm solving with a suitable cross-body approach. Attempts replace the pending plan. Read-only: returns plan_id when feasible. Use robot.execute with that plan_id to perform requested movement.'),
            ('robot.execute',self.execute,schema(plan_id=string), 'Physically execute the current successful plan through the real robot bridge, using a fixed 3-second trajectory. No terminal or keyboard step is required. Only accepts the latest plan_id from this task, once; refresh or replanning invalidates it. Returns measured arrival feedback, not a command echo. Does not open/close the gripper. Use only for movement requested by the user; observation or planning alone does not require execution.'),
            ('robot.arm_state',self.state,schema(), 'Read available arms, persisted needs_reset arms and current-turn IK preparation IDs. Home targets and motion parameters are managed internally. No robot connection or movement.'),
            ('robot.reset_arm',self.reset_arm,schema(arm={'type':'string','enum':self.arms}), 'Reset the selected arm six joints to home AND its gripper to the configured opening in 3 seconds. Verify joint and gripper feedback. Other arm uses current-state hold. Changing gripper opening can grip or release objects; finish placing held objects before reset. Required before switching away from a used arm. No collision planning. Gripper targets in meters (0=closed, null=hold): '+str(self.reset_settings['gripper_position_m'])),
        ]
        if self.vla:
            policies=options['vla']['policies']
            specs.append(('vla.run',self.run_vla,schema(policy_id={'type':'string','enum':list(policies)},
                preparation_id=string,duration_s={'type':'number','exclusiveMinimum':0,'maximum':max(p['max_duration_s'] for p in policies.values())}),
                'Run a configured VLA through existing LeRobot RTC. MUST first physically execute IK above the target and pass its preparation_id. Full dual-arm output, including grippers; VLA chooses the arm. Returns measured used_arms and final images. Duration is an execution budget, not task success. Policies: '+str({k:{f:p[f] for f in ('description','task','duration_s','max_duration_s')} for k,p in policies.items()})))
        self.tools=[]
        for name,method,parameters,description in specs:
            async def call(arguments, call_context, method=method):
                async with self.lock:
                    call_context.check_active()
                    return await method(arguments,call_context)
            action=name in ('robot.execute','robot.reset_arm','vla.run')
            timeout=options['inference_timeout_s']
            if name=='vla.run':timeout=options['vla']['load_timeout_s']+max(p['max_duration_s'] for p in options['vla']['policies'].values())+30
            self.tools.append(FunctionTool(ToolDefinition(name=name,description=description,
                parameters=parameters,read_only=not action,resources=['cobotmagic.snapshot','cobotmagic.robot'] if action else ['cobotmagic.snapshot'],
                timeout_s=timeout,review_outcome=name=='vla.run'),call,self.stop if action else None))

    def connection(self):
        return {'host':self.ik.settings['host'],'port':self.ik.settings['port'],
                **self.ik.options.motion.model_dump()}

    def invalidate(self):
        self.last_plan=None;self.observation=None;self.detections.clear()
        self.observation_call_id=None
        self.ik.snapshot=None;self.preparations.clear()

    async def state(self,arguments,context):
        return ToolResult(data={'available_arms':self.arms,'needs_reset':sorted(self.arm_state.dirty()),
            'preparations':{token:{'arm':receipt['arm']} for token,receipt in self.preparations.items()}})

    async def reset_arm(self,arguments,context):
        context.check_active()
        arm=arguments['arm']
        run=ResetRun(self.connection(),self.reset_settings,arm,
                     Path(self.context.workspace)/'runs'/('reset-'+uuid4().hex))
        result=await self.run_arm_motion(run,arm,context)
        if result.get('arrival_verified'):self.arm_state.reset(arm)
        return ToolResult(data=result,control_released=run.control_released)

    async def run_vla(self,arguments,context):
        context.check_active()
        receipt=self.preparations.get(arguments['preparation_id'])
        if receipt is None:raise ValueError('VLA requires verified IK arrival above the target; call robot.execute first')
        policy=self.options['vla']['policies'][arguments['policy_id']]
        if not 0<arguments['duration_s']<=policy['max_duration_s']:
            raise ValueError('Duration exceeds this policy execution budget')
        self.arm_state.require([receipt['arm']])
        before=self.arm_state.dirty()
        self.invalidate()
        self.arm_state.used(['left','right'])  # Until feedback resolves which arms actually moved.
        self.vla_calls.add(context.call_id)
        result=await self.vla.run(arguments['policy_id'],arguments['duration_s'],
                                  {receipt['arm']:receipt['joints_rad']},context)
        if result.get('control_released'):
            self.arm_state.write(before|set(result.get('used_arms',['left','right'])))
        images=result.pop('images',{})
        before_images=result.pop('before_images',{})
        return ToolResult(status='error' if result.get('error') else 'ok',text=result.get('error',''),
            data=result,images=images,before_images=before_images,control_released=result.get('control_released',False))

    def current(self, token=None):
        if not self.observation:
            raise ValueError('No current image; call camera.observe or omit observation_id in perception tools to capture one')
        if token is not None and token not in (self.observation['observation_id'],self.observation_call_id):
            raise ValueError('The supplied observation_id is not the current image; omit it to use the current image, or call camera.observe')
        return self.observation

    async def image_for(self, arguments, context):
        token=arguments.get('observation_id')
        if token is None and self.observation is None:
            await self.observe({},context)
        return self.current(token)

    async def observe(self, arguments, context):
        self.last_plan=None
        self.observation=None
        self.observation_call_id=None
        self.detections.clear()
        captured=await self.ik.capture()
        context.check_active()
        frame=self.ik.snapshot['frame']
        token=captured['observation_id']
        image_ref,frame_ref=f'{token}-front.jpg',f'{token}-rgb.npz'
        (self.media/image_ref).write_bytes(frame.jpeg)
        with Image.open(io.BytesIO(frame.jpeg)) as image:
            rgb=np.asarray(image.convert('RGB'))
        np.savez_compressed(self.media/frame_ref,rgb=rgb)
        self.observation={'observation_id':token,'frame_ref':frame_ref,'image_ref':image_ref,
                          'width':frame.width,'height':frame.height}
        self.observation_call_id=context.call_id
        return ToolResult(data={'observation_id':token,'camera':{'name':'front','placement':'fixed_front'}},
                          images={'front':image_ref})

    async def segment(self, arguments, context):
        observation=await self.image_for(arguments,context)
        self.last_plan=None
        query=' '.join(arguments['query'].split()).casefold()
        # A scene can contain several targets. Retrying this query replaces its
        # own token (even on failure), not detections of other objects.
        self.detections={token:detection for token,detection in self.detections.items()
                         if detection['query']!=query}
        result=await infer(self.process,'segment',{'frame_ref':observation['frame_ref'],'query':arguments['query']},context)
        images=result.pop('images',{})
        if result.get('found'):
            pixel=np.asarray(result['pixel_uv'],dtype=float)
            if pixel.shape!=(2,) or not np.isfinite(pixel).all() or not (0<=pixel[0]<observation['width'] and 0<=pixel[1]<observation['height']):
                raise ValueError('Detector returned invalid original-image coordinates')
            if result.get('frame_ref')!=observation['frame_ref']:
                raise ValueError('Detector returned a different frame')
            evidence=arm_context(self.ik,pixel,[observation['width'],observation['height']],self.arms,
                                 self.options.get('arm_preference_distance_margin_m',.05))
            token=uuid4().hex
            self.detections[token]={'observation_id':observation['observation_id'],
                                    'pixel_uv':pixel.tolist(),'query':query,'arm_context':evidence}
            result['detection_id']=token
            result['arm_context']=evidence
        public={key:value for key,value in result.items() if key not in ('frame_ref','mask_ref')}
        return ToolResult(data={**public,'observation_id':observation['observation_id']},
                          images={'front':observation['image_ref'],**images})

    async def depth(self, arguments, context):
        observation=await self.image_for(arguments,context)
        result=await infer(self.process,'depth',{'frame_ref':observation['frame_ref']},context)
        if result.get('frame_ref')!=observation['frame_ref']:
            raise ValueError('Depth prediction returned a different frame')
        images=result.pop('images',{})
        public={key:value for key,value in result.items() if key not in ('frame_ref','depth_ref')}
        return ToolResult(images={'front':observation['image_ref'],**images},
                          data={**public,'observation_id':observation['observation_id']})

    async def plan(self, arguments, context):
        self.last_plan=None
        detection=self.detections.get(arguments['detection_id'])
        if detection is None:
            raise ValueError('Unknown detection; identify the object first')
        self.current(detection['observation_id'])
        arm=arguments.get('arm',self.arm)
        if arm not in self.arms:
            raise ValueError('Select an available arm for this plan')
        self.arm_state.require([arm])
        target=PixelTarget(observation_id=detection['observation_id'],arm=arm,
                           u=detection['pixel_uv'][0],v=detection['pixel_uv'][1])
        result=await asyncio.to_thread(self.ik.calculate,target)
        context.check_active()
        if result['execution_ready']:
            result['plan_id']=uuid4().hex
            self.last_plan=result
        # Dense plotting coordinates belong to the saved review bundle. They
        # previously buried plan_id behind tens of thousands of characters,
        # forcing the model to page through session history to find its result.
        keys=('plan_id','arm','solved','execution_ready','blockers','target_xyz_m',
              'duration_s','target_downward_angle_deg','jaw_heading_preserved',
              'path_mode','path_points','compensation','bundle','command','preview_command',
              'start_joint_limit_projection_deg')
        summary={key:result[key] for key in keys if key in result}
        return ToolResult(data={**summary,'arm_context':detection['arm_context'],
                                'detection_id':arguments['detection_id'],'robot_moved':False,
                                'depth_prediction_used_for_ik':False})

    async def execute(self, arguments, context):
        context.check_active()
        plan=self.last_plan
        if not plan or not plan['execution_ready'] or arguments['plan_id']!=plan['plan_id']:
            raise ValueError('Unknown or superseded plan_id; generate a new plan in this task')
        self.arm_state.require([plan['arm']])
        # Consume before dispatch, including failed/uncertain attempts. Never replay a motion.
        self.last_plan=None
        self.preparations.clear()
        run=MotionRun(plan['bundle'])
        result=await self.run_arm_motion(run,plan['arm'],context)
        if result.get('arrival_verified'):
            import json
            saved=json.loads((Path(plan['bundle'])/'plan.json').read_text())
            token=uuid4().hex
            self.preparations[token]={'arm':plan['arm'],'joints_rad':result.get('measured_joints_rad',saved['joint_path_rad'][-1]),
                                      'detection_id':plan.get('detection_id',arguments['plan_id'])}
            result['preparation_id']=token
        return ToolResult(data={'plan_id':arguments['plan_id'],**result},control_released=run.control_released)

    async def run_arm_motion(self,run,arm,context):
        before=self.arm_state.dirty()
        self.arm_state.used([arm])  # Persist intent before a possible controller write.
        try:
            return await self.run_motion(run,context)
        finally:
            if run.sent or run.completed:
                self.invalidate()
            else:
                # A rejected precheck changed neither the scene nor arm handover state.
                self.arm_state.write(before)

    async def run_motion(self,run,context):
        self.motion_runs[context.call_id]=run
        run.task=asyncio.create_task(asyncio.to_thread(run.run))
        try:
            result=await asyncio.shield(run.task)
        except asyncio.CancelledError:
            run.cancelled.set()
            await asyncio.shield(asyncio.gather(run.task,return_exceptions=True))
            raise
        return result

    async def stop(self, call_id, reason):
        if call_id in self.vla_calls:return await self.vla.stop(call_id)
        run=self.motion_runs.get(call_id)
        if run is None:
            return True
        run.cancelled.set()
        await asyncio.shield(asyncio.gather(run.task,return_exceptions=True))
        return run.control_released

    async def close(self):
        if self.vla:await self.vla.finish_turn(self.vla_calls)
        for run in self.motion_runs.values():
            if not run.task.done():run.cancelled.set()
        await asyncio.gather(*(r.task for r in self.motion_runs.values()),return_exceptions=True)
        try:
            await self.process.close()
        finally:
            await self.ik.bridge.close()
