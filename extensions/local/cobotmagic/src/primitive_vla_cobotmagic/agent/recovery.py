"""Operator recovery: fence the old worker, then only READ robot feedback."""
from asyncio import sleep
from time import monotonic
from uuid import uuid4

import numpy as np

from primitive_vla.runtime.service import TaskConflict
from primitive_vla.tools import CallContext
from ..bridge import CameraBridge
from ..ik.config import load_pixel_settings
from ..ik.kinematics import vector
from ..vla.client import close_workspace


async def stationary_feedback(bridge, *, duration_s=1., timeout_s=6.):
    deadline = monotonic()+timeout_s
    context = CallContext('recovery-'+uuid4().hex, deadline)
    previous_stamps = None
    window = []
    window_started = None
    while monotonic() < deadline:
        began = monotonic()
        raw = await bridge.observation(context)
        elapsed = monotonic()-began
        stamp = raw.get('timestamp_ns')
        if raw.get('ok') is not True or type(stamp) is not int or stamp <= 0:
            raise ValueError('桥未返回有效反馈，控制锁保持')
        stamps = [raw.get('joint_timestamps_ns', {}).get(side) for side in ('left','right')]
        if any(type(s) is not int or s <= 0 or not -.05 <= (stamp-s)/1e9 <= .5-elapsed for s in stamps):
            raise ValueError('关节反馈过期或时间戳无效，控制锁保持')
        if previous_stamps is not None and any(s <= old for s,old in zip(stamps,previous_stamps)):
            raise ValueError('关节时间戳未持续更新，无法确认当前状态，控制锁保持')
        previous_stamps = stamps
        positions = np.array([vector(raw.get('positions',{}).get(side),7,side+' feedback')
                              for side in ('left','right')])
        now = monotonic()
        if window_started is None:window_started = now
        window.append(positions)
        spread = np.ptp(np.asarray(window),axis=0)
        # Across the whole window, not only adjacent samples (slow motion counts).
        if np.max(spread[:,:6]) > .005 or np.max(spread[:,6]) > .001:
            window, window_started = [positions], now
        elif len(window) >= 5 and now-window_started >= duration_s:
            return {'stationary_feedback':True, 'window_s':now-window_started,
                    'samples':len(window), 'joint_timestamps_ns':dict(zip(('left','right'),stamps)),
                    'positions':dict(zip(('left','right'),positions.tolist())),
                    'max_joint_range_rad':float(np.max(spread[:,:6])),
                    'max_gripper_range_m':float(np.max(spread[:,6]))}
        await sleep(.1)
    raise ValueError('未观察到连续稳定的关节反馈，控制锁保持；请确认机器人已停稳后重试')


async def recover_control(service, ik_config, *, confirmed_stopped):
    if confirmed_stopped is not True:
        raise TaskConflict('请先确认机器人已停止，且其他 VLA/遥操作控制程序已退出')
    if service.busy:
        raise TaskConflict('任务仍在运行或取消处理中，请等待结束后恢复')
    if not service.store.control_locked:
        return {'control_locked':False, 'recovered':False, 'reason':'控制未锁定'}
    # Completed tasks have closed their local runners; reap the cached worker
    # before checking telemetry so an old VLA cannot resume sending commands.
    await close_workspace(service.workspace)
    _, options = load_pixel_settings(ik_config)
    bridge = CameraBridge(options.host,options.port,connect_timeout_s=2,request_timeout_s=2)
    try:
        evidence = await stationary_feedback(bridge)
    finally:
        await bridge.close()
    if service.busy:
        raise TaskConflict('任务状态已变化，未解除控制锁')
    evidence.update(operator_confirmed_stopped=True, operator_confirmed_other_controllers_exited=True,
                    worker_fenced=True, source='operator_web_recovery', robot_moved_by_recovery=False)
    record = service.store.record_control_recovery(evidence)
    return {'control_locked':False,'recovered':True,'recovery':record,
            'reason':'控制已恢复。旧任务未重跑，请重新下达指令。'}
