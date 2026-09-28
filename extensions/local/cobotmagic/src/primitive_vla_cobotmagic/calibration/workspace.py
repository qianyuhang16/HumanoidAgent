"""Named calibration runs and a single active, read-only capture session."""

import asyncio
from contextlib import suppress
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import shutil
from uuid import uuid4

from .core import ACCEPTANCE_KEYS, Collector, Dataset, read_config, solve_session, write_json
from .tcp import TCP_POLICY, TCPCollector, TCPDataset, solve_tcp_session

STEPS = {
    'intrinsics': ('前置相机内参（双臂共用）', 'camera-intrinsics/front', 'intrinsics', 'right'),
    'left': ('前置相机 → 左臂基座', 'handeye/front-to-left-base', 'handeye', 'left'),
    'right': ('前置相机 → 右臂基座', 'handeye/front-to-right-base', 'handeye', 'right'),
    'tcp_left': ('左臂 TCP · 固定点标定', 'tcp/left-tool', 'tcp', 'left'),
    'tcp_right': ('右臂 TCP · 固定点标定', 'tcp/right-tool', 'tcp', 'right'),
}


def read_result(folder, acceptance_limits=None):
    path = folder / 'result.json'
    if not path.exists():
        return None
    result = json.loads(path.read_text())
    ids = {p.parent.name for p in (folder / 'samples').glob('*/sample.json')}
    solved_ids = set(result.get('training_ids', []) + result.get('validation_ids', []))
    changed = bool(result.get('phase') in ('handeye', 'tcp') and acceptance_limits and
                   any(result.get('thresholds', {}).get(k) != v for k, v in acceptance_limits.items()))
    return {**result, 'thresholds_changed': changed,
            'stale': changed or (bool(ids != solved_ids) if 'training_ids' in result else False)}


class CalibrationWorkspace:
    def __init__(self, root, config):
        self.root = Path(root).resolve()
        shared = read_config(config)
        self.configs = {arm: {**deepcopy(shared), 'arm': arm} for arm in ('left', 'right')}
        self.root.mkdir(parents=True, exist_ok=True)
        for _, path, _, _ in STEPS.values():
            (self.root / path / 'runs').mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / 'workspace.json'
        self.index = json.loads(self.index_path.read_text()) if self.index_path.exists() else {
            'schema_version': 1, 'active_runs': {}, 'imports': {}}
        self.collector = None
        self.step = None
        self.revision = uuid4().hex
        self.task = None
        self.running = False
        self.session_lock = None
        self.workspace_lock = None
        self.lock = asyncio.Lock()

    def folder(self, step, run):
        if step not in STEPS or not run or Path(run).name != run or run in ('.', '..'):
            raise ValueError('无效的标定步骤或批次')
        return self.root / STEPS[step][1] / 'runs' / run

    def persist(self):
        write_json(self.index_path, self.index)

    def acquire(self):
        self.workspace_lock = (self.root / '.workspace.lock').open('a')
        try:
            fcntl.flock(self.workspace_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.workspace_lock.close()
            self.workspace_lock = None
            raise ValueError('这个工作区已被另一个标定服务使用')
        if self.index_path.exists():
            self.index = json.loads(self.index_path.read_text())

    def import_legacy(self):
        """Copy completed legacy sessions once; never rename or modify source data."""
        for name, step in (('intrinsics', 'intrinsics'), ('handeye-left', 'left'), ('handeye', 'right')):
            source = self.root / name
            if name in self.index['imports'] or not (source / 'manifest.json').exists():
                continue
            manifest = json.loads((source / 'manifest.json').read_text())
            if manifest['phase'] != STEPS[step][2] or manifest['config']['arm'] != STEPS[step][3]:
                raise ValueError(f'旧目录 {name} 的模式/机械臂与名称不符，未导入')
            # Refuse a snapshot while an old capture process may still be writing.
            with (source / '.capture.lock').open('a') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise ValueError(f'请先 Ctrl+C 停止旧采集服务：{source}') from error
                run = 'imported-' + uuid4().hex[:8]
                target = self.folder(step, run)
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name('.pending-' + run)
                try:
                    shutil.copytree(source, temporary, ignore=shutil.ignore_patterns('.capture.lock', '.pending-*'))
                    temporary.rename(target)
                finally:
                    if temporary.exists():
                        shutil.rmtree(temporary)
            write_json(target / 'import-source.json', {'source': str(source)})
            self.index['imports'][name] = str(target.relative_to(self.root))
            self.index['active_runs'].setdefault(step, run)
            self.persist()

    def check_revision(self, revision):
        if revision != self.revision:
            raise ValueError('当前步骤或批次已切换，请等待页面刷新后重试')

    def current_intrinsics(self):
        run = self.index['active_runs'].get('intrinsics')
        result = read_result(self.folder('intrinsics', run)) if run else None
        if not result or not result.get('accepted') or result.get('stale'):
            raise ValueError('请先完成并求解前置相机内参，结果需通过检查且未新增待求解样本')
        # UI-only metadata must not change the intrinsic snapshot stored in manifests.
        return {k: v for k, v in result.items() if k not in ('stale', 'thresholds_changed')}

    def prepare(self, step, run, new):
        if step not in STEPS:
            raise ValueError('无效的标定步骤')
        _, _, phase, arm = STEPS[step]
        config = deepcopy(self.configs[arm])
        intrinsics = None
        if new:
            run = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid4().hex[:6]
            if phase == 'handeye':
                intrinsics = self.current_intrinsics()
        else:
            run = run or self.index['active_runs'].get(step)
            if not run:
                raise ValueError('此步骤还没有批次，请点击“新建批次”')
            folder = self.folder(step, run)
            manifest = json.loads((folder / 'manifest.json').read_text())
            if manifest['phase'] != phase or manifest['config']['arm'] != arm:
                raise ValueError('批次的机械臂/模式与步骤不匹配')
            intrinsics = manifest['intrinsics']
        dataset = (TCPDataset(self.folder(step, run), config) if phase == 'tcp' else
                   Dataset(self.folder(step, run), config, phase, intrinsics))
        return run, dataset

    async def stop_collector(self):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None
        if self.collector:
            await self.collector.bridge.close()
        if self.session_lock:
            self.session_lock.close()
            self.session_lock = None

    async def select(self, step, run=None, new=False):
        # Caller holds self.lock; validate before releasing the previous session.
        run, dataset = self.prepare(step, run, new)
        if self.collector and dataset.folder == self.collector.dataset.folder:
            return
        lock = (dataset.folder / '.capture.lock').open('a')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            lock.close()
            raise ValueError('这个批次已有采集服务，请先停止旧服务') from error
        await self.stop_collector()
        self.session_lock = lock
        self.collector = TCPCollector(dataset) if dataset.phase == 'tcp' else Collector(dataset)
        self.step = step
        self.revision = uuid4().hex
        self.index['active_runs'][step] = run
        self.persist()
        if self.running:
            self.task = asyncio.create_task(self.collector.poll())

    def acceptance_limits(self, step):
        _, _, phase, arm = STEPS[step]
        if phase == 'tcp':
            return {'max_point_error_m': TCP_POLICY['max_point_error_m']}
        return {k: self.configs[arm]['quality'][k] for k in ACCEPTANCE_KEYS}

    def state(self):
        steps = []
        for key, (label, path, _, arm) in STEPS.items():
            runs = []
            for folder in sorted((self.root / path / 'runs').glob('*'), reverse=True):
                if (folder / 'manifest.json').exists():
                    runs.append({'id': folder.name, 'result': read_result(folder, self.acceptance_limits(key))})
            steps.append({'id': key, 'label': label, 'runs': runs,
                          'active_run': self.index['active_runs'].get(key)})
        capture = self.collector.state() if self.collector else None
        return {'revision': self.revision, 'step': self.step, 'workspace': str(self.root),
                'board': self.configs['right']['board'],
                'session': str(self.collector.dataset.folder) if self.collector else None,
                'steps': steps, 'capture': capture,
                'result': read_result(self.collector.dataset.folder, self.acceptance_limits(self.step)) if self.collector else None}

    async def solve(self):
        if not self.collector:
            raise ValueError('请先选择标定批次')
        # Pause polling while solving; mutations are serialised by self.lock.
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None
        if self.collector.dataset.phase == 'tcp':
            work = asyncio.to_thread(solve_tcp_session, self.collector.dataset.folder)
        else:
            work = asyncio.to_thread(solve_session, self.collector.dataset.folder,
                acceptance_limits={k: self.collector.config['quality'][k] for k in ACCEPTANCE_KEYS})
        solver = asyncio.create_task(work)
        try:
            try:
                return await asyncio.shield(solver)
            except asyncio.CancelledError:
                # The worker may still be writing its result; retain locks until it finishes.
                with suppress(Exception):
                    await solver
                raise
        finally:
            self.collector.latest = None
            self.collector.history.clear()
            if self.running:
                self.task = asyncio.create_task(self.collector.poll())

    async def close(self):
        await self.stop_collector()
        if self.workspace_lock:
            self.workspace_lock.close()
            self.workspace_lock = None
