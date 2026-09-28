"""Runs inside the existing LeRobot environment. No hardware activity at import."""
import gc
import json
import math
import time
from pathlib import Path
from uuid import uuid4

SIDES=('left','right')
def joints(obs,side):return [float(obs[f'{side}_joint_{i}.pos']) for i in range(1,7)]

def active_arms(before,after,tolerance=.03,gripper_tolerance=.003):
    return [s for s in SIDES if max(abs(a-b) for a,b in zip(joints(before,s),joints(after,s)))>tolerance
            or abs(float(before[f'{s}_gripper.pos'])-float(after[f'{s}_gripper.pos']))>gripper_tolerance]

class Backend:
    def __init__(self,options,connection,media):
        self.options,self.connection,self.media=options,connection,Path(media)
        self.loaded=None;self.policy=None;self.pre=None;self.post=None;self.poisoned=False

    def load(self,policy_id):
        if self.poisoned:raise RuntimeError('Previous RTC thread did not stop; restart VLA worker')
        if self.loaded==policy_id:return
        import torch
        from lerobot.configs.policies import PreTrainedConfig
        from lerobot.rollout.context import _load_pretrained_policy
        from lerobot.policies import make_pre_post_processors
        from lerobot.rollout.inference.rtc import supports_rtc_inference
        from lerobot.policies.rtc.configuration_rtc import RTCConfig
        from lerobot.configs import RTCAttentionSchedule
        self.policy=self.pre=self.post=None;self.loaded=None
        gc.collect();torch.cuda.empty_cache()
        spec=self.options['policies'][policy_id]
        cfg=PreTrainedConfig.from_pretrained(spec['checkpoint'])
        cfg.pretrained_path=Path(spec['checkpoint']);cfg.device=self.options['device']
        cfg.compile_model=False
        policy=_load_pretrained_policy(cfg)
        if not supports_rtc_inference(policy):raise ValueError('Policy does not support RTC')
        rtc=RTCConfig(execution_horizon=self.options['execution_horizon'],
            max_guidance_weight=self.options['max_guidance_weight'],
            prefix_attention_schedule=RTCAttentionSchedule(self.options['prefix_attention_schedule']))
        policy.config.rtc_config=rtc
        if hasattr(policy,'init_rtc_processor'):policy.init_rtc_processor()
        self.policy=policy.to(self.options['device']).eval()
        self.pre,self.post=make_pre_post_processors(policy_cfg=cfg,pretrained_path=cfg.pretrained_path,
            preprocessor_overrides={'device_processor':{'device':self.options['device']},
                                    'rename_observations_processor':{'rename_map':{}}})
        self.loaded=policy_id

    def run(self,request,cancel):
        from lerobot.robots.magicbot import MagicBot,MagicBotConfig
        from lerobot.rollout.robot_wrapper import ThreadSafeRobot
        from lerobot.rollout.inference.rtc import RTCInferenceEngine
        from lerobot.utils.feature_utils import hw_to_dataset_features
        from lerobot.processor import make_default_processors
        from PIL import Image
        connection=self.connection
        class FeedbackRobot(MagicBot):
            def _decode_observation(self,response):
                stamp=response.get('timestamp_ns')
                stamps=response.get('joint_timestamps_ns',{})
                if type(stamp) is not int:raise ValueError('Missing feedback timestamp')
                for side in SIDES:
                    joint_stamp=stamps.get(side)
                    if type(joint_stamp) is not int or not -.05<=(stamp-joint_stamp)/1e9<=connection.get('max_feedback_age_s',.2):
                        raise ValueError('Stale joint feedback')
                    if joint_stamp<getattr(self,'feedback_stamps',{}).get(side,0):
                        raise ValueError('Joint feedback timestamp moved backwards')
                self.feedback_stamps=dict(stamps)
                return super()._decode_observation(response)
        spec=self.options['policies'][request['policy_id']]
        result={'executed':False,'actions_sent':0,'control_released':True,'task_success':None,
                'used_arms':[],'policy_id':request['policy_id']}
        robot=engine=thread=None;obs=initial=None;used=set();sent=False
        run_id=uuid4().hex
        folder=self.media.parent/'runs'/('vla-'+run_id)
        folder.mkdir(parents=True,exist_ok=True)
        trace=(folder/'actions.jsonl').open('w')
        result['trace']=str(folder/'actions.jsonl')
        def record_used():
            if initial is not None:
                used.update(active_arms(initial,obs,self.options.get('used_joint_threshold_rad',.03),
                                        self.options.get('used_gripper_threshold_m',.003)))
        def check():
            if cancel.is_set():raise RuntimeError('VLA cancelled')
            if time.time()>=request['deadline']:raise TimeoutError('VLA tool deadline expired')
        try:
            check();self.load(request['policy_id']);check()
            robot=FeedbackRobot(MagicBotConfig(host=self.connection['host'],port=self.connection['port'],
                speed_percent=self.connection['speed_percent'],request_timeout_s=2.))
            robot.connect();check()
            obs=initial=robot.get_observation()
            # A saved IK receipt must still agree with feedback after model loading.
            for side,expected in request['prepared_joints'].items():
                if max(abs(a-b) for a,b in zip(joints(obs,side),expected))>self.options['preparation_tolerance_rad']:
                    raise ValueError('Robot moved since IK preparation; perform IK again')
            keys=list(robot.action_features)
            expected=[f'{s}_{j}.pos' for s in SIDES for j in [*[f'joint_{i}' for i in range(1,7)],'gripper']]
            training=list(self.policy.config.action_feature_names or [])
            legacy=[f'arm.jointStatePosition.master{s}.joint{i}' for s in ('Left','Right') for i in range(7)]
            if keys!=expected or (training and training not in (expected,legacy)):
                raise ValueError('Unverified policy/hardware action ordering')
            if self.policy.config.output_features['action'].shape[0]!=14:
                raise ValueError('MagicBot requires a 14-dimensional policy')
            # Capture actual pre-action views after model loading/preflight. The
            # Agent's last image can precede IK and is not an execution baseline.
            self.media.mkdir(parents=True,exist_ok=True)
            result['before_images']={}
            for name in ('front','left','right'):
                if name in obs:
                    ref=f'{run_id}-vla-before-{name}.jpg'
                    Image.fromarray(obs[name]).save(self.media/ref)
                    result['before_images'][name]=ref
            _,action_processor,observation_processor=make_default_processors()
            wrapper=ThreadSafeRobot(robot)
            engine=RTCInferenceEngine(self.policy,self.pre,self.post,wrapper,self.policy.config.rtc_config,
                hw_to_dataset_features(robot.observation_features,'observation'),spec['task'],
                self.options['fps'],self.options['device'],rtc_queue_threshold=self.options['queue_threshold'])
            engine.reset();engine.start();thread=engine._rtc_thread;engine.resume()
            began=time.monotonic();first_action=None
            while True:
                check()
                tick=time.monotonic()
                if first_action is not None and tick-first_action>=request['duration_s']:break
                if first_action is None and tick-began>=self.options['load_timeout_s']:
                    raise TimeoutError('RTC produced no actions')
                if engine.failed:raise RuntimeError('RTC inference failed')
                obs=robot.get_observation();record_used()
                processed=observation_processor(obs);engine.notify_observation(processed)
                action=engine.get_action(None)
                if action is not None:
                    values=action.detach().cpu().flatten().tolist()
                    if len(values)!=14 or not all(math.isfinite(v) for v in values):
                        raise ValueError('Invalid RTC action')
                    command=action_processor(({k:v for k,v in zip(keys,values)},obs))
                    check();sent=True;result['control_released']=False
                    trace.write(json.dumps({'time':time.time(),'command':command},allow_nan=False)+'\n');trace.flush()
                    robot.send_action(command)  # Preserve both arms and grippers, unchanged LeRobot mapping.
                    result['actions_sent']+=1
                    if first_action is None:first_action=time.monotonic()
                cancel.wait(max(0,1/self.options['fps']-(time.monotonic()-tick)))
            result.update(executed=bool(result['actions_sent']),stop_reason='duration_elapsed',
                          elapsed_s=time.monotonic()-first_action)
        except Exception as error:
            result.update(error=str(error),stop_reason='cancelled' if cancel.is_set() else 'error')
        finally:
            # Only this thread sends actions. Closing the loop above fences all future policy output.
            if engine is not None:
                try:
                    engine.stop()
                    if thread is not None and thread.is_alive():self.poisoned=True
                    else:engine.reset()
                except Exception as error:
                    self.poisoned=True
                    result['error']=f'RTC cleanup failed: {error}'
            if robot is not None and robot.is_connected:
                try:
                    obs=robot.get_observation()
                    record_used()
                    if sent:
                        # Hold measured state, then verify stationary feedback instead of trusting an ack.
                        hold={k:float(obs[k]) for k in robot.action_features}
                        robot.send_action(hold)
                        end=time.monotonic()+2.;stable=0;previous=obs
                        stamps=dict(robot.feedback_stamps)
                        while time.monotonic()<end:
                            time.sleep(.05);obs=robot.get_observation()
                            record_used()
                            near=all(max(abs(a-b) for a,b in zip(joints(obs,s),joints(hold,s)))<.03 for s in SIDES)
                            still=all(max(abs(a-b) for a,b in zip(joints(obs,s),joints(previous,s)))<.002 for s in SIDES)
                            grip=all(abs(float(obs[f'{s}_gripper.pos'])-max(0.,min(.08,hold[f'{s}_gripper.pos'])))<.005 for s in SIDES)
                            fresh=all(robot.feedback_stamps[s]>stamps[s] for s in SIDES)
                            stamps=dict(robot.feedback_stamps)
                            stable=stable+1 if near and still and grip and fresh else 0;previous=obs
                            if stable>=3:result['control_released']=True;break
                    result['last_feedback']={s:joints(obs,s) for s in SIDES}
                    self.media.mkdir(parents=True,exist_ok=True)
                    images={}
                    for name in ('front','left','right'):
                        if name in obs:
                            ref=f"{run_id}-vla-{name}.jpg"
                            Image.fromarray(obs[name]).save(self.media/ref);images[name]=ref
                    result['images']=images
                except Exception as error:
                    result['feedback_error']=str(error)
                finally:robot.disconnect()
            result['used_arms']=sorted(used)
            result['worker_reusable']=not self.poisoned
            if sent and not result['control_released']:
                result['error']=result.get('error','Robot stop not confirmed by feedback')
            trace.close()
            (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        return result
