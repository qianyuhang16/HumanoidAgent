"""版本 / 身份 / 坐标 / 时间 / 资源的绑定校验。

职责边界（对应 01_TASK_SPEC.md 第 2 节第 2、3 条）：

* 结构性不合法（缺字段、类型错、版本/坐标系/单位/身份域不一致、悬空资源引用）
  -> 抛 EvidenceBindingError，整体拒配，不产生任何 Verification。
* 关联不上（观察早于动作结束、call/visit/attempt 对不上、观察的不是本次目标对象）
  -> 记为 unpaired（拒配该条观察），整体仍可返回「证据合法但不可判」的 uncertain。
* 预览帧 -> 直接丢弃并留痕，绝不自动升格为正式任务证据。

不混用 D3 / Scene0011 / Scene200 的对象 ID，不补造任何坐标变换。
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Any

from .errors import EvidenceBindingError

CASE_SCHEMA = "t05.effect_case.v1"
CONTEXT_SCHEMA = "t05.task_context.v1"
VERDICT_SCHEMA = "t05.effect_verdict.v1"
ORIGINS = {"real", "synthetic"}
SOURCE_KINDS = {
    "instance_geometry", "image_observation", "depth_scan", "cavity_sensing",
    "relation_fact", "coverage", "tool_return", "preview",
}
PAYLOAD_KINDS = {"cavity", "object_pose", "coverage", "relation", "other"}

# 这些字段必须逐字相等，任何一个不同就不能把观察与动作证据拼在一起。
HARD_IDENTITY_KEYS = (
    "scene_version", "map_version", "coordinate_frame", "length_unit",
    "identity_scope", "session_id", "task_id",
)

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _require(mapping: Any, key: str, types: tuple[type, ...], where: str) -> Any:
    if not isinstance(mapping, dict):
        raise EvidenceBindingError("format_invalid", f"{where} 必须是对象")
    if key not in mapping:
        raise EvidenceBindingError("missing_field", f"{where} 缺字段 {key}")
    value = mapping[key]
    # bool 是 int 的子类，单独排除，避免把 true 当数字用。
    if isinstance(value, bool) or not isinstance(value, types):
        names = "/".join(t.__name__ for t in types)
        raise EvidenceBindingError("format_invalid", f"{where}.{key} 类型应为 {names}")
    return value


def _number(mapping: dict, key: str, where: str) -> float:
    value=float(_require(mapping, key, (int, float), where))
    if not math.isfinite(value):raise EvidenceBindingError('format_invalid',f'{where}.{key} must be finite')
    return value


def _validate_context(case: dict) -> dict:
    ctx = _require_field(case, "task_context", dict)
    schema = _require(ctx, "schema_version", (str,), "task_context")
    if schema != CONTEXT_SCHEMA:
        raise EvidenceBindingError("schema_mismatch", f"task_context.schema_version={schema!r}，期望 {CONTEXT_SCHEMA}")
    for key in ("task_id", "session_id", "scene_version", "map_version",
                "coordinate_frame", "length_unit", "identity_scope", "goal", "box_object_id"):
        _require(ctx, key, (str,), "task_context")
    items = _require(ctx, "items", (dict,), "task_context")
    for item in ("甲", "乙"):
        _require(items, item, (str,), "task_context.items")

    action = _require(ctx, "action", (dict,), "task_context")
    for key in ("call_id", "action", "item", "object_id", "tool_status"):
        _require(action, key, (str,), "task_context.action")
    _require(action, "visit", (int,), "task_context.action")
    _require(action, "attempt", (int,), "task_context.action")
    _number(action, "end_time", "task_context.action")
    if action["item"] not in items:
        raise EvidenceBindingError("binding_mismatch", "action.item 不在 task_context.items 中")
    if action["object_id"] != items[action["item"]]:
        raise EvidenceBindingError("binding_mismatch", "action.object_id 与 items 中该物件的 ID 不一致")

    tol = _require(ctx, "tolerance", (dict,), "task_context")
    band = _number(tol, "boundary_band_m", "task_context.tolerance")
    margin = _number(tol, "inside_margin_m", "task_context.tolerance")
    coverage = _number(tol, "coverage_required", "task_context.tolerance")
    basis = _require(tol, "basis", (str,), "task_context.tolerance")
    if band <= 0 or margin < 0 or not 0 < coverage <= 1 or not basis.strip():
        raise EvidenceBindingError("format_invalid", "tolerance 参数不合法或 basis（依据）为空")
    return ctx


def _require_field(raw: Any, key: str, typ: type) -> Any:
    if not isinstance(raw, dict) or key not in raw or not isinstance(raw[key], typ):
        raise EvidenceBindingError("missing_field", f"case 缺字段 {key} 或类型不对")
    return raw[key]


def _validate_resources(raw: Any, origin: str) -> dict[str, dict]:
    items = _require_field(raw, "resources", list)
    table: dict[str, dict] = {}
    for i, ref in enumerate(items):
        where = f"resources[{i}]"
        rid = _require(ref, "id", (str,), where)
        uri = _require(ref, "uri", (str,), where)
        _require(ref, "media_type", (str,), where)
        metadata = ref.get("metadata", {})
        if not isinstance(metadata, dict):
            raise EvidenceBindingError("format_invalid", f"{where}.metadata 必须是对象")
        if rid in table:
            raise EvidenceBindingError("duplicate_resource_id", f"重复资源 id：{rid}")
        if origin == "real":
            digest = metadata.get("sha256")
            if not isinstance(digest, str) or not SHA256_RE.match(digest):
                raise EvidenceBindingError("missing_real_sha",
                                           f"origin=real 的资源 {rid} 必须给出真实 SHA-256，不允许留空")
        elif "sha256" in metadata:
            raise EvidenceBindingError("fake_sha_for_synthetic",
                                       f"origin=synthetic 的资源 {rid} 不得填写 SHA-256 冒充真实来源")
        if origin == "synthetic" and not uri.startswith("synthetic://"):
            raise EvidenceBindingError("fake_uri_for_synthetic",
                                       f"origin=synthetic 的资源 {rid} 的 uri 必须用 synthetic:// 前缀")
        table[rid] = ref
    return table


@dataclass(frozen=True)
class BoundObservation:
    raw: dict
    observation_id: str
    captured_at: float
    source_kind: str
    payload: dict
    resource_refs: tuple[str, ...]

    def brief(self) -> dict:
        return {
            "observation_id": self.observation_id,
            "captured_at": self.captured_at,
            "source_kind": self.source_kind,
            "related_call_id": self.raw.get("related_call_id"),
            "visit": self.raw.get("visit"),
            "attempt": self.raw.get("attempt"),
            "payload_kind": self.payload.get("kind"),
            "resource_refs": list(self.resource_refs),
        }


@dataclass(frozen=True)
class Binding:
    context: dict
    observations: tuple[BoundObservation, ...]
    unpaired: tuple[dict, ...]
    dropped_preview: tuple[dict, ...]
    resources: dict[str, dict]
    origin: str

    @property
    def action(self) -> dict:
        return self.context["action"]

    @property
    def tolerance(self) -> dict:
        return self.context["tolerance"]


def _validate_observation(raw: Any, index: int, resources: dict[str, dict]) -> BoundObservation:
    where = f"observations[{index}]"
    obs_id = _require(raw, "observation_id", (str,), where)
    source_kind = _require(raw, "source_kind", (str,), where)
    if source_kind not in SOURCE_KINDS:
        raise EvidenceBindingError("unknown_source_kind", f"{where}.source_kind={source_kind!r} 不在允许集合内")
    captured_at = _number(raw, "captured_at", where)
    for key in HARD_IDENTITY_KEYS:
        _require(raw, key, (str,), where)
    _require(raw, "related_call_id", (str,), where)
    _require(raw, "visit", (int,), where)
    _require(raw, "attempt", (int,), where)
    payload = _require(raw, "payload", (dict,), where)
    kind = _require(payload, "kind", (str,), "payload")
    if kind not in PAYLOAD_KINDS:
        raise EvidenceBindingError("unknown_payload_kind", f"{where}.payload.kind={kind!r} 不在允许集合内")
    refs = _require(raw, "resource_refs", (list,), where)
    if any(not isinstance(ref, str) for ref in refs):
        raise EvidenceBindingError("format_invalid", f"{where}.resource_refs 必须是字符串列表")
    for ref in refs:
        if ref not in resources:
            raise EvidenceBindingError("dangling_resource_ref",
                                       f"{where} 引用了不存在的资源 {ref!r}，无法追溯原件")
    return BoundObservation(raw, obs_id, captured_at, source_kind, payload, tuple(refs))


def bind_case(case: Any) -> Binding:
    """校验并绑定一个 case；不合法直接抛 EvidenceBindingError。"""
    schema = _require_field(case, "schema_version", str)
    if schema != CASE_SCHEMA:
        raise EvidenceBindingError("schema_mismatch", f"case.schema_version={schema!r}，期望 {CASE_SCHEMA}")
    origin = _require_field(case, "origin", str)
    if origin not in ORIGINS:
        raise EvidenceBindingError("format_invalid", f"origin 必须是 real 或 synthetic，收到 {origin!r}")
    context = _validate_context(case)
    resources = _validate_resources(case, origin)
    raw_observations = _require_field(case, "observations", list)

    action = context["action"]
    target_object = action["object_id"]
    box_id = context["box_object_id"]

    usable: list[BoundObservation] = []
    unpaired: list[dict] = []
    dropped: list[dict] = []

    for index, raw in enumerate(raw_observations):
        obs = _validate_observation(raw, index, resources)
        if obs.source_kind == "preview":
            dropped.append({"observation_id": obs.observation_id,
                            "reason": "预览帧按框架约定不能自动作为正式任务证据"})
            continue
        for key in HARD_IDENTITY_KEYS:
            if obs.raw[key] != context[key]:
                raise EvidenceBindingError(
                    "binding_mismatch",
                    f"观察 {obs.observation_id} 的 {key}={obs.raw[key]!r} 与任务上下文 {context[key]!r} 不一致；"
                    "拒绝跨版本/跨坐标系拼接，也不补造坐标变换",
                )
        reason = None
        if obs.captured_at <= action["end_time"]:
            reason = "observation_before_action_end：观察早于/等于动作结束时间，属于旧观察"
        elif obs.raw["related_call_id"] != action["call_id"]:
            reason = "call_id_mismatch：关联的是旧 call 或别的动作"
        elif obs.raw["visit"] != action["visit"] or obs.raw["attempt"] != action["attempt"]:
            reason = "visit_attempt_mismatch：不是同一次 visit/attempt"
        elif obs.payload.get("kind") == "object_pose" and obs.payload.get("object_id") != target_object:
            reason = "object_id_not_target：观察的不是本次放置的目标物件"
        elif obs.payload.get("kind") in {"cavity", "coverage"} and obs.payload.get("box_object_id") != box_id:
            reason = "box_id_mismatch：观察的不是任务绑定的开放盒"
        if reason:
            unpaired.append({"observation_id": obs.observation_id, "reason": reason})
            continue
        usable.append(obs)

    usable.sort(key=lambda o: (o.captured_at, o.observation_id))
    return Binding(context, tuple(usable), tuple(unpaired), tuple(dropped), resources, origin)


def resolve_refs(ids: tuple[str, ...] | list[str], resources: dict[str, dict]) -> list[dict]:
    """把资源 id 还原成完整 ResourceRef 形状的字典（保留原件引用，不改写）。"""
    return [resources[i] for i in ids]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
