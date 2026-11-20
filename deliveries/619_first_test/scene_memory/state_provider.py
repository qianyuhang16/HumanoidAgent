"""U265 bounded read-only adapter; original SceneMemory and framework contracts.

This module needs no jsonschema registry import. Direct component execution is
not proof that the dependency-qualified Router/Hook/Memory chain ran.
"""
from __future__ import annotations

import copy
from hashlib import sha256
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from primitive_vla.contracts import Domain
from primitive_vla.tools.contracts import CallContext, ToolResult, ToolSpec
from scene_memory import SceneMemory

TOOL_NAME = 'state.scene_query'
ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = 'artifacts/auto_memory_replay_u224/module_a/outputs/automatic_snapshot.json'
Text = Annotated[str, StringConstraints(strict=True, min_length=1)]
Digest = Annotated[str, StringConstraints(strict=True, pattern=r'^[0-9a-f]{64}$')]

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, allow_inf_nan=False)

class ProjectBinding(StrictModel):
    project_task_id: Text
    visit_id: Text
    round: int = Field(ge=1)
    map_version: Text
    snapshot_sha256: Digest
    instruction: Text
    constraints: tuple[Text, ...] = Field(min_length=1, strict=False)
    input_driver: Literal['scripted_local_query_not_user_model_or_deployment']

class QueryBinding(StrictModel):
    project_task_id: Text
    visit_id: Text
    round: int = Field(ge=1)
    map_version: Text
    snapshot_sha256: Digest

class QueryTask(StrictModel):
    target_id: Text | None = None
    target_label: Text | None = None
    attributes: dict[Text, Text] = Field(default_factory=dict)
    required_attributes: list[Text] = Field(default_factory=list)
    required_fact_ids: list[Text] = Field(default_factory=list)
    required_capabilities: list[Text] = Field(default_factory=list)

    @model_validator(mode='after')
    def one_target_selector(self):
        if (self.target_id is None) == (self.target_label is None):
            raise ValueError('Supply exactly one target_id or target_label; unknown is a label, not verified identity')
        return self

class QueryArguments(StrictModel):
    query_id: Text
    task: QueryTask
    region_id: Text
    token_budget: int = Field(ge=0, le=1000000)
    binding: QueryBinding

class DerivedQueryArguments(StrictModel):
    query_id: Text
    task: QueryTask
    region_id: Text
    token_budget: int = Field(ge=0, le=1000000)
    expected_revision: Text

class SceneQueryProvider:
    @classmethod
    def from_derived(cls, child: SceneMemory, *, session_id: str, task_id: str, revision: str):
        from .task_effect.evolution import validate_child
        validate_child(child, session_id=session_id, task_id=task_id, revision=revision)
        provider = cls.__new__(cls)
        provider.framework_session_id = session_id
        provider.framework_task_id = task_id
        provider.memory = child
        provider._derived = True
        return provider

    def validate_switch(self, child: SceneMemory, *, expected_parent: str):
        from .task_effect.evolution import validate_child
        if expected_parent != self.memory.snapshot.map_version:
            raise ValueError('provider parent revision changed')
        transition = validate_child(child, session_id=self.framework_session_id,
            task_id=self.framework_task_id, revision=child.snapshot.map_version)
        if transition.parent_version != expected_parent or child._parent_scene is not self.memory:
            raise ValueError('only the directly bound parent child may be published')
        return transition

    def switch_scene(self, child: SceneMemory, *, expected_parent: str):
        # No await or external mutation between validation and local publication.
        # Host validates its ledger in the same synchronous transaction first.
        transition = self.validate_switch(child, expected_parent=expected_parent)
        self.memory = child
        self._derived = True
        return transition

    def _derived_spec(self):
        text = {'type':'string','minLength':1}
        fields = {
            'schema_version':{'const':'task-scene-state.v1'}, 'query_status':text,
            'context_json':text, 'context_sha256':{'type':'string','pattern':'^[0-9a-f]{64}$'},
            'context_utf8_bytes':{'type':'integer','minimum':0},
            'budget_kind':{'const':'utf8_byte_upper_bound'}, 'query_id':text,
            'query_task':{'type':'object'}, 'region_id':text,
            'scene_version':text, 'base_map_version':text,
            'framework_binding':{'type':'object'}, 'transition':{'type':'object'},
            'read_only':{'const':True}, 'physical_completion_verified':{'const':False}}
        return ToolSpec(name=TOOL_NAME, domain=Domain.STATE, provider='go2.task_scene.readonly',
            description='Query the explicitly bound derived task scene. expected_revision must match the current '
                'session/task head. Includes original unknowns, source competition and verified transition lineage.',
            input_schema=DerivedQueryArguments.model_json_schema(),
            output_schema={'type':'object','additionalProperties':False,'properties':fields,'required':list(fields)},
            required_resources=(), execution_mode='sync', timeout=5.0)

    async def _execute_derived(self, arguments, context):
        from dataclasses import asdict
        from .task_effect.evolution import validate_child
        args = DerivedQueryArguments.model_validate(arguments)
        if (context.session_id, context.task_id) != (self.framework_session_id, self.framework_task_id):
            raise ValueError('wrong task/session for derived query')
        if not context.call_id or context.cancel_requested.is_set():
            raise ValueError('missing call ID or cancelled query')
        child = self.memory
        transition = validate_child(child, session_id=context.session_id, task_id=context.task_id,
                                    revision=args.expected_revision)
        if args.region_id != child.snapshot.region_id:
            raise ValueError('wrong derived scene region')
        task = copy.deepcopy(arguments['task'])
        result = child.query_context(task,args.region_id,args.token_budget)
        raw = result.context_json()
        return ToolResult(payload={'schema_version':'task-scene-state.v1','query_status':result.status,
            'context_sha256':sha256(raw.encode()).hexdigest(), 'context_utf8_bytes':result.utf8_bytes,
            'budget_kind':result.budget_kind, 'query_id':args.query_id,'query_task':task,'region_id':args.region_id,
            'scene_version':transition.scene_version,'base_map_version':transition.base_map_version,
            'framework_binding':{'session_id':context.session_id,'task_id':context.task_id,
                                 'call_id':context.call_id,'robot_id':context.robot_id},
            'transition':asdict(transition),'read_only':True,'physical_completion_verified':False,'context_json':raw})

    def __init__(self, snapshot_path: Path, project_binding: dict,
                 framework_session_id: str, framework_task_id: str):
        self.path = Path(snapshot_path).resolve()
        if self.path != (ROOT / SOURCE_PATH).resolve():
            raise ValueError('U265 only permits its fixed real snapshot, not caller-chosen files')
        self.project = ProjectBinding.model_validate(project_binding)
        if not all(type(x) is str and x.strip() for x in (framework_session_id, framework_task_id)):
            raise ValueError('Explicit framework session/task IDs required')
        self.framework_session_id = framework_session_id
        self.framework_task_id = framework_task_id
        data = self.checked_source()
        self.memory = SceneMemory.from_fused_snapshot(json.loads(data), facts=[], relations=[])
        if self.memory.snapshot.map_version != self.project.map_version:
            raise ValueError('Project scene version differs from the fixed source')

    def checked_source(self) -> bytes:
        data = self.path.read_bytes()
        if sha256(data).hexdigest() != self.project.snapshot_sha256:
            raise ValueError('Snapshot changed; stop and rebind explicitly')
        return data

    def spec(self) -> ToolSpec:
        # Registry validators are compiled once. Keep one stable contract through
        # archive -> child -> grandchild switches without touching registry internals.
        if hasattr(self, '_tool_spec'):
            return self._tool_spec
        derived = self._derived_spec()
        if getattr(self, '_derived', False):
            self._tool_spec = derived
        else:
            from dataclasses import replace
            archive = self._archive_spec()
            branches = []
            definitions = {}
            for schema in (archive.input_schema, derived.input_schema):
                schema = copy.deepcopy(schema)
                for name, definition in schema.pop('$defs', {}).items():
                    if name in definitions and definitions[name] != definition:
                        raise ValueError('conflicting query schema definitions')
                    definitions[name] = definition
                branches.append(schema)
            input_schema = {'type':'object','oneOf':branches,'$defs':definitions}
            self._tool_spec = replace(archive,
                description=archive.description + ' After explicit verified child publication, use expected_revision '
                    'with the derived query contract; stale archive bindings are rejected.',
                input_schema=input_schema,
                output_schema={'oneOf':[archive.output_schema,derived.output_schema]})
        return self._tool_spec

    def _archive_spec(self) -> ToolSpec:
        text = {'type': 'string', 'minLength': 1}
        fields = {
            'schema_version': {'const': 'u265-scene-state.v1'},
            'query_status': text, 'context_json': text, 'context_sha256': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'},
            'context_utf8_bytes': {'type': 'integer', 'minimum': 0},
            'budget_kind': {'const': 'utf8_byte_upper_bound'},
            'query_id': text, 'query_task': {'type': 'object'},
            'region_id': text, 'token_budget': {'type': 'integer', 'minimum': 0, 'maximum': 1000000},
            'project_binding': ProjectBinding.model_json_schema(),
            'framework_binding': {'type': 'object', 'additionalProperties': False,
                'properties': {k: text for k in ('session_id', 'task_id', 'call_id', 'robot_id')},
                'required': ['session_id', 'task_id', 'call_id', 'robot_id']},
            'source': {'type': 'object', 'additionalProperties': False,
                'properties': {'path': {'const': SOURCE_PATH}, 'sha256': {'const': self.project.snapshot_sha256},
                    'schema_version': {'const': self.memory.snapshot.schema_version}},
                'required': ['path', 'sha256', 'schema_version']},
            'read_only': {'const': True}, 'physical_completion_verified': {'const': False},
        }
        return ToolSpec(name=TOOL_NAME, domain=Domain.STATE, provider='go2.u265.readonly_scene',
            description='Read original archived SceneMemory query context for the bound task/scene. '
                'Returns unchanged context_json and query_status (including ambiguous/missing_facts/needs_more_budget), '
                'unknowns and original source/ID mappings. Never writes facts, resolves physical identity, plans or acts. '
                'token_budget is the original UTF8-byte proxy; wrapper metadata is outside that budget. '
                'Binding mismatch requires explicit rebinding, not an automatic retry.',
            input_schema=QueryArguments.model_json_schema(),
            output_schema={'type': 'object', 'additionalProperties': False,
                'properties': fields, 'required': list(fields)},
            required_resources=(), execution_mode='sync', timeout=5.0)

    async def execute(self, arguments: dict, context: CallContext) -> ToolResult:
        if getattr(self, '_derived', False):
            return await self._execute_derived(arguments, context)
        args = QueryArguments.model_validate(arguments)
        if (context.session_id, context.task_id) != (self.framework_session_id, self.framework_task_id):
            raise ValueError('Wrong framework session/task; no query was performed')
        if not context.call_id or context.cancel_requested.is_set():
            raise ValueError('Missing call ID or cancelled local query')
        expected = {k: getattr(self.project, k) for k in QueryBinding.model_fields}
        if args.binding.model_dump() != expected:
            raise ValueError('Wrong project task/visit/round/scene/source; no query was performed')
        if args.region_id != self.memory.snapshot.region_id:
            raise ValueError('Wrong region for this bound archive')
        self.checked_source()
        # Forward the caller's validated original mapping, not model-dump defaults.
        raw_task = copy.deepcopy(arguments['task'])
        result = self.memory.query_context(raw_task, args.region_id, args.token_budget)
        raw_context = result.context_json()
        payload = {'schema_version': 'u265-scene-state.v1', 'query_status': result.status,
            'context_json': raw_context, 'context_sha256': sha256(raw_context.encode('utf-8')).hexdigest(),
            'context_utf8_bytes': result.utf8_bytes, 'budget_kind': result.budget_kind,
            'query_id': args.query_id, 'query_task': raw_task, 'region_id': args.region_id,
            'token_budget': args.token_budget, 'project_binding': self.project.model_dump(mode='json'),
            'framework_binding': {'session_id': context.session_id, 'task_id': context.task_id,
                'call_id': context.call_id, 'robot_id': context.robot_id},
            'source': {'path': SOURCE_PATH, 'sha256': self.project.snapshot_sha256,
                'schema_version': self.memory.snapshot.schema_version},
            'read_only': True, 'physical_completion_verified': False}
        # Preserve the complete small boundary before the large archived context.
        # The original ContextBuilder previews prefixes; no fields/values change.
        payload = {**{k: v for k, v in payload.items() if k != 'context_json'}, 'context_json': raw_context}
        return ToolResult(payload=payload, resource_refs=(), verification=None)

    async def cancel(self, context: CallContext) -> bool:
        # Synchronous read-only query owns no hardware/control resource. This
        # return value is not evidence of a robot stopping.
        return True
