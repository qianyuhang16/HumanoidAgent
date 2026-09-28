"""Read-only fixed-point TCP calibration; no camera calibration or robot commands.

For each measured flange pose: R_i @ tcp_in_tip + t_i = fixed_point_in_base.
Only training poses fit the two unknown points. Validation poses check the fit.
The TCP frame inherits the tip axes by convention; its orientation is not fitted.
"""

import json
from pathlib import Path
import shutil
import time
from uuid import uuid4

import cv2
import numpy as np

from ..bridge import decode_frames, timestamp
from .core import Collector, digest, diversity, limit_warning, measured_fk, write_json
from ..ik.kinematics import SerialIK, vector

TCP_POLICY = {'min_train': 4, 'min_validation': 2, 'max_point_error_m': .015,
              'max_gripper_change_m': .001, 'min_pose_rotation_deg': 3., 'max_condition': 100.}
CONFIG_KEYS = ('host', 'port', 'camera', 'arm', 'urdf', 'base_link', 'tip_link', 'joint_names', 'capture')


def chain_for(config, urdf=None):
    return SerialIK(urdf or config['urdf'], config['base_link'], config['tip_link'],
                    config['joint_names'], np.eye(4))


def fit_tcp(poses):
    poses = np.asarray(poses, float)
    if poses.ndim != 3 or poses.shape[1:] != (4, 4) or len(poses) < 4 or not np.isfinite(poses).all():
        raise ValueError('TCP 至少需要 4 组有效训练姿态')
    for pose in poses:
        if (not np.allclose(pose[3], [0, 0, 0, 1]) or
                not np.allclose(pose[:3,:3].T @ pose[:3,:3], np.eye(3), atol=1e-5) or
                np.linalg.det(pose[:3,:3]) < .999):
            raise ValueError('TCP 输入不是有效刚体位姿')
    try:
        rotation_diversity = diversity(poses)
    except ValueError as error:
        raise ValueError('姿态变化不足：保持同一点对准，增加前后和左右倾斜，不能只平移或只绕一个轴转') from error
    a = np.vstack([np.column_stack((pose[:3,:3], -np.eye(3))) for pose in poses])
    b = np.concatenate([-pose[:3,3] for pose in poses])
    solution, _, rank, singular = np.linalg.lstsq(a, b, rcond=None)
    condition = float(singular[0] / max(singular[-1], 1e-15))
    if rank != 6 or condition > TCP_POLICY['max_condition']:
        raise ValueError('TCP 求解不稳定：请增加不同方向的倾斜姿态')
    return solution[:3], solution[3:], condition, rotation_diversity


class TCPDataset:
    phase = 'tcp'

    def __init__(self, folder, config):
        self.folder = Path(folder).resolve()
        self.config = {key: config[key] for key in CONFIG_KEYS}
        self.chain = chain_for(self.config)
        self.policy = dict(TCP_POLICY)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.folder/'manifest.json'
        manifest = {'schema_version': 1, 'phase': 'tcp', 'config': self.config, 'intrinsics': None,
                    'tcp_policy': self.policy, 'urdf_sha256': digest(Path(config['urdf']).read_bytes()),
                    'robot_control': False, 'method': 'fixed_point_least_squares'}
        if self.manifest_path.exists():
            old = json.loads(self.manifest_path.read_text())
            def identity(value):
                # Acceptance can change without changing measured poses or the model.
                return {**value, 'config': {k:v for k,v in value['config'].items() if k != 'capture'},
                        'tcp_policy': {k:v for k,v in value['tcp_policy'].items() if k != 'max_point_error_m'}}
            if identity(old) != identity(manifest):
                raise ValueError('TCP 模型、机械臂或求解设置改变，请新建批次')
        else:
            if any(self.folder.iterdir()):
                raise ValueError('TCP 目录非空且没有 manifest，请新建批次')
            shutil.copyfile(config['urdf'], self.folder/'robot.urdf')
            write_json(self.manifest_path, manifest)
        (self.folder/'samples').mkdir(exist_ok=True)
        self.samples = [json.loads(p.read_text()) for p in sorted((self.folder/'samples').glob('*/sample.json'))]

    def save(self, sample, jpeg, split):
        if split not in ('train', 'validation'):
            raise ValueError('无效的采样用途')
        if self.samples and abs(sample['gripper_position_m']-self.samples[0]['gripper_position_m']) > self.policy['max_gripper_change_m']:
            raise ValueError('夹爪开口与本批次不一致；恢复原开口或新建批次')
        rotation = np.array(sample['T_base_from_tip'])[:3,:3]
        for previous in self.samples:
            if sample['joint_timestamp_ns'] == previous['joint_timestamp_ns']:
                raise ValueError('这条关节反馈已经记录')
            old_rotation = np.array(previous['T_base_from_tip'])[:3,:3]
            angle = np.rad2deg(np.linalg.norm(cv2.Rodrigues(rotation @ old_rotation.T)[0]))
            if angle < self.policy['min_pose_rotation_deg']:
                raise ValueError('与已存姿态朝向太相似；换一个朝向后重新对准同一固定点')
        row = {**sample, 'id':uuid4().hex, 'split':split, 'image_sha256':digest(jpeg),
               'capture_policy':dict(self.config['capture']), 'alignment_confirmed_by':'operator record action'}
        temporary = self.folder/('.pending-'+row['id'])
        temporary.mkdir()
        try:
            (temporary/'image.jpg').write_bytes(jpeg)
            write_json(temporary/'sample.json', row)
            temporary.rename(self.folder/'samples'/row['id'])
        finally:
            if temporary.exists(): shutil.rmtree(temporary)
        self.samples.append(row)
        return row['id']


class TCPCollector(Collector):
    """Reuse read-only polling, freshness-at-record, state and disconnect handling."""

    def ingest(self, reply, elapsed):
        now = time.monotonic()
        limits, arm = self.config['capture'], self.config['arm']
        sampled, frames = decode_frames(reply, [self.config['camera']], limits['max_age_s'], 8*1024*1024)
        frame = frames[0]
        stamp = timestamp(reply.get('joint_timestamps_ns', {}).get(arm), 'joint timestamp')
        feedback = vector(reply.get('positions', {}).get(arm), 7, 'joint feedback')
        joint_age = (sampled-stamp)/1e9
        age = max(joint_age, frame.age_ms/1000)+elapsed
        if min(joint_age, frame.age_ms/1000) < -.05 or age > limits['max_age_s']:
            raise ValueError('图像或关节反馈过期')
        skew = abs(stamp-frame.timestamp_ns)/1e9
        if skew > limits['max_skew_s']:
            raise ValueError(f'图像/关节时间差 {skew:.3f}s 超出采样上限 {limits["max_skew_s"]:.3f}s')
        if self.history and (stamp < self.history[-1][1] or frame.timestamp_ns < self.history[-1][2]):
            raise ValueError('ROS 时间戳倒退，请重启采集')
        self.history.append((now, stamp, frame.timestamp_ns, feedback.copy()))
        while len(self.history)>1 and self.history[1][0] < now-limits['settle_s']:
            self.history.popleft()
        variations = np.ptp(np.array([item[3] for item in self.history]), axis=0)
        stable = (len(self.history)>=3 and now-self.history[0][0]>=limits['settle_s'] and
                  stamp>self.history[0][1] and frame.timestamp_ns>self.history[0][2] and
                  np.max(variations[:6])<=limits['max_joint_motion_rad'] and
                  variations[6]<=self.dataset.policy['max_gripper_change_m'])
        reason = None if stable else '等待机械臂和夹爪停稳；请保持同一工具点对准固定点'
        if self.dataset.samples and abs(feedback[6]-self.dataset.samples[0]['gripper_position_m']) > self.dataset.policy['max_gripper_change_m']:
            reason = '夹爪开口改变；恢复原开口或新建 TCP 批次'
        pose, violations = measured_fk(self.dataset.chain, feedback[:6])
        sample = {'image_timestamp_ns':frame.timestamp_ns, 'joint_timestamp_ns':stamp,
            'bridge_timestamp_ns':sampled, 'timestamp_skew_s':skew, 'transport_elapsed_s':elapsed,
            'joints_rad':feedback[:6].tolist(), 'gripper_position_m':float(feedback[6]),
            'T_base_from_tip':pose.tolist(), 'urdf_limit_exceedances':violations,
            'camera':self.config['camera'], 'arm':arm, 'image_size':[frame.width, frame.height]}
        self.latest = {'sample':sample, 'jpeg':frame.jpeg, 'overlay':frame.jpeg,
                       'received':now, 'age_s':age, 'reason':reason}
        self.message = reason or '已停稳：确认同一工具点已对准固定点，再按空格记录；V 记录独立验证姿态'
        if violations: self.message += '\n'+limit_warning(violations)


def solve_tcp_session(folder, max_point_error_m=None):
    folder = Path(folder)
    try:
        manifest = json.loads((folder/'manifest.json').read_text())
        if manifest['phase'] != 'tcp': raise ValueError('不是 TCP 标定批次')
        config, policy = manifest['config'], dict(manifest['tcp_policy'])
        threshold = TCP_POLICY['max_point_error_m'] if max_point_error_m is None else max_point_error_m
        if type(threshold) not in (int, float) or not np.isfinite(threshold) or threshold <= 0:
            raise ValueError('TCP 点误差上限必须是有限的正数（米）')
        policy['max_point_error_m'] = threshold
        if digest((folder/'robot.urdf').read_bytes()) != manifest['urdf_sha256']:
            raise ValueError('TCP 批次的 URDF 校验失败')
        chain = chain_for(config, folder/'robot.urdf')
        rows, poses, violations = [], {}, []
        for path in sorted((folder/'samples').glob('*/sample.json')):
            row = json.loads(path.read_text())
            if row['arm'] != config['arm'] or row['split'] not in ('train','validation'):
                raise ValueError('TCP 样本机械臂或用途不匹配')
            if digest((path.parent/'image.jpg').read_bytes()) != row['image_sha256']:
                raise ValueError('TCP 样本图像校验失败')
            pose, warnings = measured_fk(chain, row['joints_rad'])
            poses[row['id']] = pose
            if warnings: violations.append({'sample_id':row['id'], 'joints':warnings})
            rows.append(row)
        train = [r for r in rows if r['split']=='train']
        validation = [r for r in rows if r['split']=='validation']
        if len(train)<policy['min_train'] or len(validation)<policy['min_validation']:
            raise ValueError(f"TCP 至少需要 {policy['min_train']} 组训练 + {policy['min_validation']} 组独立验证；当前 {len(train)} + {len(validation)}")
        openings = np.array([r['gripper_position_m'] for r in rows],float)
        if not np.isfinite(openings).all() or np.ptp(openings)>policy['max_gripper_change_m']:
            raise ValueError('TCP 样本夹爪开口不一致，请保持工具几何不变')
        tcp, point, condition, rotations = fit_tcp([poses[r['id']] for r in train])
        def residuals(group):
            return [{'sample_id':r['id'], 'translation_m':float(np.linalg.norm(
                poses[r['id']][:3,:3]@tcp+poses[r['id']][:3,3]-point))} for r in group]
        training_errors, validation_errors = residuals(train), residuals(validation)
        maximum = max(r['translation_m'] for r in training_errors+validation_errors)
        result = {'schema_version':1, 'phase':'tcp', 'method':'fixed_point_least_squares',
            'arm':config['arm'], 'base_frame':config['base_link'], 'tip_frame':config['tip_link'],
            'urdf_sha256':manifest['urdf_sha256'], 'tcp_xyz_m':tcp.tolist(),
            'tcp_rpy_rad':[0.,0.,0.], 'orientation_convention':'TCP axes inherit URDF tip axes; orientation is not fitted',
            'fixed_point_in_base_m':point.tolist(), 'offset_length_m':float(np.linalg.norm(tcp)),
            'gripper_position_m':float(np.mean(openings)),
            'training_ids':[r['id'] for r in train], 'validation_ids':[r['id'] for r in validation],
            'training_residuals':training_errors, 'validation_residuals':validation_errors,
            'thresholds':policy, 'condition_number':condition, 'rotation_diversity':rotations,
            'accepted':maximum<=policy['max_point_error_m'], 'physical_accuracy_verified':False,
            'accepted_meaning':'Same-point consistency only; operator must align the intended grasp point, not a different tool point',
            'urdf_limit_exceedances':violations,
            'warnings':['存在超出参考 URDF 范围的反馈，请核对模型与关节约定'] if violations else []}
        write_json(folder/'result.json', result)
        return result
    except (ValueError, KeyError, OSError, np.linalg.LinAlgError) as error:
        if (folder/'manifest.json').exists():
            write_json(folder/'result.json', {'phase':'tcp', 'accepted':False, 'error':str(error),
                                            'physical_accuracy_verified':False})
        raise ValueError(str(error)) from error
