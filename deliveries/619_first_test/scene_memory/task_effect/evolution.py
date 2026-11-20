"""Immutable, task-scoped revisions from the native execution/evidence journal.

This is a derived task scene, never an automatic-point-replay reclassification.
A candidate does not switch a provider or advance a working-memory ledger.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
import time
from types import MappingProxyType
from urllib.parse import unquote, urlparse

from primitive_vla.contracts import Domain
from .binding import bind_case
from .judge import judge_case


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


class FrozenDocument:
    """Detached JSON value with immutable attribute views and fresh serializations."""
    __slots__ = ('_json',)

    def __init__(self, value):
        object.__setattr__(self, '_json', canonical(value))

    def __setattr__(self, key, value):
        raise AttributeError('task scene is immutable')

    def __getattr__(self, key):
        raw = json.loads(self._json)
        if key not in raw:
            raise AttributeError(key)
        return self._freeze(raw[key])

    @classmethod
    def _freeze(cls, value):
        if isinstance(value, dict):
            return cls(value)
        if isinstance(value, list):
            return tuple(cls._freeze(v) for v in value)
        return value

    def model_dump(self, *, mode='python', exclude=None):
        raw = json.loads(self._json)
        return {k: v for k, v in raw.items() if not exclude or k not in exclude}


@dataclass(frozen=True)
class SceneTransition:
    parent_version: str
    scene_version: str
    base_map_version: str
    session_id: str
    task_id: str
    event_id: str
    related_call_id: str
    execution_record_id: str
    observation_record_id: str
    verification_record_id: str
    updated_object_ids: tuple[str, ...]
    source_refs: tuple[str, ...]


def _finite(value, name):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f'{name} must be finite')
    return value


def _scope(record, session_id, task_id, now, max_age):
    if (record.session_id, record.task_id) != (session_id, task_id):
        raise ValueError('foreign session/task evidence')
    stamp = _finite(record.timestamp, 'record timestamp')
    if stamp > now or now - stamp > max_age:
        raise ValueError('stale or future record')
    if record.expires_at is not None and _finite(record.expires_at, 'expiry') <= now:
        raise ValueError('expired evidence')
    canonical(record.to_dict())


def _resources(memory, refs, origin, allow_synthetic):
    if not refs:
        raise ValueError('source-bearing resource_refs required')
    table = {}
    for ref in refs:
        value = asdict(ref)
        if ref.id in table and table[ref.id] != value:
            raise ValueError('conflicting resource reference')
        table[ref.id] = value
        if asdict(memory.get_resource(ref.id)) != value:
            raise ValueError('registered resource differs from evidence')
        parsed = urlparse(ref.uri)
        if origin == 'synthetic':
            if not allow_synthetic or parsed.scheme != 'synthetic' or 'sha256' in ref.metadata:
                raise ValueError('synthetic evidence needs explicit gate and synthetic URI')
        elif origin == 'real':
            digest = ref.metadata.get('sha256')
            if not isinstance(digest, str) or len(digest) != 64:
                raise ValueError('real evidence SHA required')
            if parsed.scheme == 'blob':
                raw = memory.read_resource(ref.id)
            elif parsed.scheme == 'file' and not parsed.netloc:
                name = unquote(parsed.path)
                if len(name) > 2 and name[0] == '/' and name[2] == ':':
                    name = name[1:]
                path = Path(name)
                if not path.is_absolute() or str(path).startswith('\\\\'):
                    raise ValueError('local resource required')
                raw = path.read_bytes()
            else:
                raise ValueError('real resource must resolve locally; no fetching')
            if sha256(raw).hexdigest() != digest:
                raise ValueError('resource bytes/SHA mismatch')
        else:
            raise ValueError('explicit real/synthetic origin required')
    return table


def validate_evidence(parent, memory, *, session_id, task_id, related_call_id,
                      execution_record_id, observation_record_id, verification_record_id,
                      expected_parent, observation_sources, verification_sources,
                      execution_sources, allow_synthetic=False, max_age_seconds=300, now=None):
    now = time.time() if now is None else _finite(now, 'now')
    if type(allow_synthetic) is not bool or _finite(max_age_seconds, 'max age') <= 0:
        raise ValueError('explicit fixture gate and positive age limit required')
    for sources in (observation_sources, verification_sources, execution_sources):
        if not isinstance(sources, (tuple, frozenset)) or not sources or any(type(s) is not str or not s.strip() for s in sources):
            raise ValueError('explicit nonempty source allowlists required')
    if any(type(s) is not str or not s.strip() for s in (session_id, task_id, related_call_id, expected_parent)):
        raise ValueError('explicit scene/session/task/call binding required')
    if expected_parent != parent.snapshot.map_version:
        raise ValueError('old parent scene')
    if parent.snapshot.schema_version == 'task-scene.v1':
        if (parent.transition.session_id, parent.transition.task_id) != (session_id, task_id):
            raise ValueError('parent belongs to another task/session')
    ex, ob, vr = (memory.evidence(i) for i in (execution_record_id, observation_record_id, verification_record_id))
    for record in (ex, ob, vr):
        _scope(record, session_id, task_id, now, max_age_seconds)
    if (ex.source not in execution_sources or ex.domain != Domain.EXECUTION or
            ex.event_type != 'tool_finished' or ex.status != 'completed' or ex.call_id != related_call_id):
        raise ValueError('same-call completed native execution required')
    if ob.source not in observation_sources or ob.domain != Domain.STATE or ob.event_type != 'observation':
        raise ValueError('allowlisted native observation required')
    if (vr.source not in verification_sources or vr.domain != Domain.VERIFICATION or
            vr.event_type != 'verification' or vr.status != 'completed'):
        raise ValueError('successful native Verification required')
    case = ob.payload['li_effect_case']
    from .errors import EvidenceBindingError
    try:
        binding = bind_case(case)
    except EvidenceBindingError as exc:
        raise ValueError('scene evidence binding rejected: ' + str(exc)) from exc
    ctx, action = binding.context, binding.action
    base = getattr(parent.snapshot, 'base_map_version', parent.snapshot.map_version)
    expected = dict(session_id=session_id, task_id=task_id, scene_version=expected_parent,
                    map_version=base, coordinate_frame=parent.snapshot.coordinate_frame,
                    length_unit=parent.snapshot.length_unit, identity_scope=parent.snapshot.identity_scope)
    if any(ctx[k] != value for k, value in expected.items()) or ob.payload.get('scene_version') != expected_parent:
        raise ValueError('scene/map/frame/unit/identity/session/task binding mismatch')
    if action['end_time'] < ex.timestamp or action['end_time'] > ob.timestamp:
        raise ValueError('case action end does not cover actual completed execution')
    item, box = action['object_id'], ctx['box_object_id']
    if item not in parent.objects or box not in parent.objects or item == box:
        raise ValueError('original distinct object/container IDs required')
    if (action['call_id'] != related_call_id or action['tool_status'] != 'completed' or
            action['action'] != 'place' or type(action['visit']) is not int or action['visit'] < 1 or
            type(action['attempt']) is not int or action['attempt'] < 1):
        raise ValueError('action call/visit/attempt/completion mismatch')
    # Unpaired/preview inputs cannot be silently dropped while publishing a new scene.
    if binding.unpaired or binding.dropped_preview or not binding.observations:
        raise ValueError('unpaired or preview observations cannot evolve scene')
    for obs in binding.observations:
        if (not obs.resource_refs or obs.captured_at <= ex.timestamp or obs.captured_at > ob.timestamp or
                now - obs.captured_at > max_age_seconds):
            raise ValueError('observation must be fresh and captured after actual execution completion')
    if not ex.timestamp < ob.timestamp <= vr.timestamp:
        raise ValueError('native evidence order mismatch')
    resources = _resources(memory, ob.resource_refs, binding.origin, allow_synthetic)
    if resources != binding.resources:
        raise ValueError('case references differ from source record')
    result = vr.payload.get('result') or {}
    payload, verification = result.get('payload', {}), result.get('verification') or {}
    if (payload.get('case_record_id') != ob.id or payload.get('item_id') != item or
            payload.get('receptacle_id') != box or payload.get('scene_version') != expected_parent or
            payload.get('origin') != binding.origin or verification.get('subject') != item or
            verification.get('conclusion') != 'success' or verification.get('related_call_id') != related_call_id or
            verification.get('failure_type') is not None):
        raise ValueError('Verification proposition/case/call mismatch or unsuccessful result')
    verdict = judge_case(case)
    if verdict['status'] != 'ok' or verdict['conclusion'] != 'success' or payload.get('li_verdict') != verdict:
        raise ValueError('original observations do not support the recorded inside verdict')
    verified_refs = verification.get('evidence', [])
    if (not verified_refs or any(r.get('id') not in resources or resources[r['id']] != r for r in verified_refs)
            or not set(verdict['evidence_ids']).issubset({r['id'] for r in verified_refs})):
        raise ValueError('Verification evidence does not cover original observation resources')
    native_refs = _resources(memory, vr.resource_refs, binding.origin, allow_synthetic)
    if any(native_refs.get(r['id']) != r for r in verified_refs):
        raise ValueError('Verification record has missing resources')
    return binding, payload, (ex, ob, vr)


def _scene_digest(scene):
    return sha256(canonical({'snapshot':scene.snapshot.model_dump(mode='json'),
        'relations':[asdict(r) for r in scene.relations],
        'facts':[asdict(f) for f in scene.facts]}).encode()).hexdigest()


def evolve_verified(parent, memory, *, session_id, task_id, event_id, related_call_id,
                    execution_record_id, observation_record_id, verification_record_id,
                    expected_parent, observation_sources, verification_sources,
                    execution_sources, allow_synthetic=False, max_age_seconds=300, now=None):
    from scene_memory import SceneMemory, Relation
    if type(event_id) is not str or not event_id.strip():
        raise ValueError('event ID required')
    # Immutable parent payloads stay unchanged. This private reservation only prevents
    # conflicting sibling candidates/replays; publication belongs to the host.
    reservations = parent.__dict__.get('_evolution_reservations', {})
    key = (session_id, task_id)
    if key in reservations:
        raise ValueError('parent already consumed: replay or conflicting child')
    ancestor = parent
    while getattr(ancestor, 'transition', None) is not None:
        if (ancestor.transition.event_id == event_id or ancestor.transition.related_call_id == related_call_id
                or ancestor.transition.execution_record_id == execution_record_id
                or ancestor.transition.observation_record_id == observation_record_id
                or ancestor.transition.verification_record_id == verification_record_id):
            raise ValueError('event/action already applied in lineage')
        ancestor = ancestor._parent_scene
    options = dict(session_id=session_id, task_id=task_id, related_call_id=related_call_id,
        execution_record_id=execution_record_id, observation_record_id=observation_record_id,
        verification_record_id=verification_record_id, expected_parent=expected_parent,
        observation_sources=observation_sources, verification_sources=verification_sources,
        execution_sources=execution_sources, allow_synthetic=allow_synthetic, max_age_seconds=max_age_seconds)
    binding, payload, records = validate_evidence(parent, memory, now=now, **options)
    item, box = binding.action['object_id'], binding.context['box_object_id']
    raw = parent.snapshot.model_dump(mode='json')
    if raw['schema_version'] == 'automatic-point-replay.v1':
        from core.automatic_point_replay_contracts import AutomaticPointReplaySnapshot
        AutomaticPointReplaySnapshot.model_validate(raw)
    # Keep immutable original geometry separately from evolving object fields.
    raw.setdefault('source_objects', json.loads(canonical(raw['objects'])))
    base = raw.get('base_map_version', raw['map_version'])
    refs = tuple(sorted({r.id for r in records[1].resource_refs}))
    poses = {o.observation_id: o for o in binding.observations if o.payload['kind'] == 'object_pose'}
    coverages = payload.get('verified_predicates', [])
    if not isinstance(coverages, list):
        raise ValueError('explicit predicate coverage must be a list')
    selected_poses = []
    for coverage in coverages:
        if (not isinstance(coverage, dict) or coverage.get('predicate') != 'bbox' or coverage.get('object_id') != item
                or coverage.get('observation_id') not in poses):
            raise ValueError('unsupported or unmatched predicate coverage')
        obs = poses[coverage['observation_id']]
        if obs.source_kind not in {'instance_geometry', 'depth_scan'}:
            raise ValueError('bbox needs qualified original geometry observation')
        center, half = obs.payload.get('center'), obs.payload.get('half_extent')
        if (not isinstance(center, list) or not isinstance(half, list) or len(center) != 3 or len(half) != 3
                or any(type(x) not in (int, float) or not math.isfinite(x) for x in center + half) or any(x <= 0 for x in half)):
            raise ValueError('bbox requires positive finite full geometry')
        if not set(obs.resource_refs).issubset({r['id'] for r in records[2].payload['result']['verification']['evidence']}):
            raise ValueError('bbox observation not covered by Verification evidence')
        selected_poses.append((center, half, obs))
    if len(selected_poses) > 1:
        raise ValueError('duplicate/conflicting bbox updates')
    qualifications = raw.get('object_state_qualifications', {})
    qualifications[item] = {'bbox_current':bool(selected_poses),'bbox_verified':bool(selected_poses),
        'orientation_verified':False, 'related_call_id':related_call_id,
        'observation_record_id':observation_record_id, 'verification_record_id':verification_record_id}
    raw['object_state_qualifications'] = qualifications
    origins = sorted(set(raw.get('evidence_origins', [])) | {binding.origin})
    raw.update(evidence_origins=origins, synthetic_lineage='synthetic' in origins)
    objects = raw['objects']
    if selected_poses:
        center, half, obs = selected_poses[0]
        target = next(o for o in objects if o['object_id'] == item)
        target.update(bbox_center=center, bbox_extent=[2*x for x in half],
                      evidence_refs=list(dict.fromkeys(target['evidence_refs'] + list(obs.resource_refs))))
    removed = tuple(r for r in parent.relations if item in (r.subject_id, r.object_id))
    relations = tuple(r for r in parent.relations if r not in removed) + (Relation(item, 'inside', box, refs),)
    # Spatial/pose facts become history. Non-spatial facts such as protection stay.
    spatial = {'on', 'inside', 'near', 'support', 'position', 'pose', 'bbox', 'location', 'holding', 'grasped'}
    invalid_facts = tuple(f for f in parent.facts if f.subject_id == item and f.predicate in spatial)
    facts = tuple(f for f in parent.facts if f not in invalid_facts)
    digest = sha256(canonical({'parent':expected_parent,'session':session_id,'task':task_id,'event':event_id,
                              'records':[r.to_dict() for r in records],'coverage':coverages}).encode()).hexdigest()
    revision = 'task-scene-' + digest
    transition = SceneTransition(expected_parent, revision, base, session_id, task_id, event_id,
        related_call_id, execution_record_id, observation_record_id, verification_record_id, (item,), refs)
    history = raw.get('transition_history', [])
    history.append({'transition':asdict(transition),'prior_objects':[o.model_dump(mode='json') for o in parent.snapshot.objects if o.object_id == item],
                    'invalidated_relations':[asdict(r) for r in removed], 'invalidated_facts':[asdict(f) for f in invalid_facts],
                    'origin':binding.origin, 'bbox_verified':bool(selected_poses)})
    raw.update(schema_version='task-scene.v1', map_version=revision, scene_version=revision,
               base_map_version=base, parent_version=expected_parent, task_session_id=session_id,
               task_id=task_id, source_schema_version=raw.get('source_schema_version',raw['schema_version']),
               transition_history=history)
    child = SceneMemory(FrozenDocument(raw), relations, facts)
    child.objects = MappingProxyType(child.objects)
    child.transition = transition
    child._parent_scene = parent
    child._parent_digest = _scene_digest(parent)
    child._native_memory = memory
    child._evidence_options = MappingProxyType(options)
    child._record_digests = tuple(sha256(canonical(r.to_dict()).encode()).hexdigest() for r in records)
    child._immutable_scene = True
    object.__setattr__(parent, '_evolution_reservations', {**reservations, key:child})
    return child


def validate_child(child, *, session_id, task_id, revision, now=None):
    """Reject caller-created copies, modified journals/resources, expiry and foreign scope."""
    transition = getattr(child, 'transition', None)
    if (not isinstance(transition, SceneTransition) or not getattr(child, '_immutable_scene', False) or
            (transition.session_id, transition.task_id, transition.scene_version) != (session_id, task_id, revision)):
        raise ValueError('genuine same-session/task derived revision required')
    parent = child._parent_scene
    if _scene_digest(parent) != child._parent_digest:
        raise ValueError('parent scene changed after candidate construction')
    if parent.__dict__.get('_evolution_reservations', {}).get((session_id, task_id)) is not child:
        raise ValueError('unregistered derived child')
    _, _, records = validate_evidence(parent, child._native_memory, now=now, **child._evidence_options)
    if tuple(sha256(canonical(r.to_dict()).encode()).hexdigest() for r in records) != child._record_digests:
        raise ValueError('native evidence changed after candidate construction')
    return transition
