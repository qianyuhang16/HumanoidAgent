"""门控 + 三态结论：把绑定后的证据算成一句可消费、可追溯的判断。

判定只发生在合法证据域内：不读人工 GT、不把工具 completed 当作效果证据、
不把预览帧当证据、不因为「想要成功」而放宽阈值。
"""

from __future__ import annotations

from typing import Any

from .binding import Binding, BoundObservation, bind_case, resolve_refs, VERDICT_SCHEMA
from .errors import EvidenceBindingError
from .geometry import evaluate_containment, poses_conflict, validate_cavity

CONCLUSION_VALUES = {"success", "failure", "uncertain"}
FAILURE_TYPE = {
    "success": None,
    "failure": "effect_failed",       # 对应 primitive_vla.contracts.Failure.EFFECT
    "uncertain": "state_uncertain",   # 对应 primitive_vla.contracts.Failure.UNCERTAIN
}


def _gate(name: str, ok: bool, detail: str) -> dict:
    return {"name": name, "ok": ok, "detail": detail}


def _collect(binding: Binding, kind: str) -> list[tuple[BoundObservation, dict]]:
    return [(obs, obs.payload) for obs in binding.observations if obs.payload.get("kind") == kind]


def _coverage_fraction(payload: dict) -> float | None:
    value = payload.get("covered_fraction")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if 0.0 <= value <= 1.0 else None


def _judge_bound(binding: Binding) -> dict:
    """绑定已经合法之后的门控与计算。"""
    origin = binding.origin
    action = binding.action
    tolerance = binding.tolerance
    subject = (f"{binding.context['task_id']}/{action['item']}/{action['action']}"
               "_into_open_box/effect")

    pose_pairs = _collect(binding, "object_pose")
    cavity_pairs = _collect(binding, "cavity")
    coverage_pairs = _collect(binding, "coverage")
    latest_pose = pose_pairs[-1][1] if pose_pairs else None
    latest_cavity = cavity_pairs[-1][1] if cavity_pairs else None
    latest_coverage = coverage_pairs[-1][1] if coverage_pairs else None

    gates: list[dict] = []
    gates.append(_gate(
        "post_action_pose_observation", bool(pose_pairs),
        f"与该次 call/visit/attempt 同源、且动作后的甲位置观测 {len(pose_pairs)} 条"
        if pose_pairs else "动作后没有任何与本次动作同源的甲位置观测",
    ))

    cavity_ok, cavity_reason, cavity_detail = (False, "没有任何盒内空腔测量", {})
    if latest_cavity is not None:
        cavity_ok, cavity_reason, cavity_detail = validate_cavity(latest_cavity)
    gates.append(_gate("cavity_directly_measured", cavity_ok, cavity_reason))

    geometry = None
    if latest_pose is not None and cavity_ok:
        geometry = evaluate_containment(latest_cavity, latest_pose, tolerance)
        precision_ok = geometry.state != "precision_insufficient"
        gates.append(_gate("precision_sufficient", precision_ok, geometry.reason))
        pad = geometry.metrics.get("pad_m", tolerance["boundary_band_m"])
    else:
        precision_ok = False
        pad = float(tolerance["boundary_band_m"])
        gates.append(_gate("precision_sufficient", False,
                           "缺位置观测或空腔证据，尚不能进入精度/几何计算"))

    conflict, worst_distance = poses_conflict([p for _, p in pose_pairs], pad) if len(pose_pairs) > 1 else (False, 0.0)
    gates.append(_gate(
        "pose_consistency", not conflict,
        f"甲的多条位置观测最大间距 {worst_distance} m，阈值 2*pad={2 * pad:.4f} m"
        if len(pose_pairs) > 1 else "只有一条位置观测，无冲突可检",
    ))

    fraction = _coverage_fraction(latest_coverage) if latest_coverage is not None else None
    required = float(tolerance["coverage_required"])
    coverage_ok = fraction is not None and fraction >= required
    gates.append(_gate(
        "coverage_of_cavity", coverage_ok,
        f"盒内区域覆盖度 {fraction} / 要求 {required}（仅在需要下 failure 时必需）"
        if fraction is not None else "缺盒内区域覆盖度说明，不能排除遮挡或另一实例",
    ))

    # ---- 三态判定：只在合法且足够的证据域内下结论 ----
    if not pose_pairs:
        conclusion = "uncertain"
        reason = ("动作后未获得与本次放甲动作同源的甲位置观测，无法判断甲是否入盒；"
                  "工具 completed 只是调用结束，不构成效果证据")
        if binding.unpaired:
            reasons = "; ".join(item["reason"].split("：")[0] for item in binding.unpaired)
            reason += f"；本次 {len(binding.unpaired)} 条观察被拒配（{reasons}）"
        if binding.dropped_preview:
            reason += f"；{len(binding.dropped_preview)} 条预览帧按框架约定丢弃，未作为证据"
    elif not cavity_ok:
        conclusion = "uncertain"
        reason = f"甲位置已观测，但盒内空腔证据不合格：{cavity_reason}；此时不能判入盒"
    elif geometry is None or geometry.state == "precision_insufficient":
        conclusion = "uncertain"
        reason = ("证据合法，但位置测量精度不足以在声明容差带内判定："
                  + (geometry.reason if geometry else "未进入几何计算"))
    elif conflict:
        conclusion = "uncertain"
        reason = f"甲的多条同源位置观测互相矛盾（最大间距 {worst_distance} m），不作单一结论"
    elif geometry.state == "inside":
        conclusion = "success"
        reason = "有证据支持甲在盒内：" + geometry.reason
    elif geometry.state == "outside" and coverage_ok:
        conclusion = "failure"
        reason = "有证据反证甲入盒：" + geometry.reason + "；且本次观察覆盖了盒内区域"
    elif geometry.state == "outside":
        conclusion = "uncertain"
        reason = ("虽观测到甲在盒外，但盒内区域覆盖度不足（"
                  f"{fraction} < {required}），不能排除遮挡或看错实例；保持不可判")
    else:
        conclusion = "uncertain"
        reason = "证据合法但处于判定边界：" + geometry.reason

    # ---- 工具 completed / 工具自述 与证据结论的关系 ----
    notes: list[str] = []
    if action.get("tool_status") == "completed" and conclusion != "success":
        notes.append("工具 completed 仅代表调用结束；本结论以动作后原始证据为准，不被工具状态覆盖")
    if action.get("tool_payload_claim") and conclusion in {"failure", "uncertain"}:
        notes.append(f"工具返回自述 {action['tool_payload_claim']!r}，与本次证据结论 {conclusion} 并存，按证据优先")
    if binding.unpaired:
        notes.append(f"{len(binding.unpaired)} 条观察因时间/调用/对象无法同源关联而被拒配，详见 unpaired")
    if binding.dropped_preview:
        notes.append(f"{len(binding.dropped_preview)} 条预览帧按框架约定丢弃，未作为正式证据")
    notes.append("本结论只覆盖「甲放置后是否入盒」单一命题，不证明两件物品均已收纳，也不评判技能调用本身")

    used_obs = [obs for obs, _ in pose_pairs + cavity_pairs + coverage_pairs]
    evidence_ids: list[str] = []
    for obs in used_obs:
        for ref in obs.resource_refs:
            if ref not in evidence_ids:
                evidence_ids.append(ref)

    return {
        "schema_version": VERDICT_SCHEMA,
        "status": "ok",
        "origin": binding.origin,
        "subject": subject,
        "conclusion": conclusion,
        "reason": reason,
        "failure_type": FAILURE_TYPE[conclusion],
        "related_call_id": action["call_id"],
        "evidence": resolve_refs(evidence_ids, binding.resources),
        "evidence_ids": evidence_ids,
        "gates": gates,
        "computation": {
            "geometry_state": geometry.state if geometry else "not_evaluated",
            "metrics": geometry.metrics if geometry else {},
            "cavity_check": cavity_detail,
            "coverage_fraction": fraction,
            "coverage_required": required,
            "pose_observation_count": len(pose_pairs),
        },
        "provenance": {
            "task_context": {
                key: binding.context[key] for key in
                ("task_id", "session_id", "scene_version", "map_version",
                 "coordinate_frame", "length_unit", "identity_scope", "goal")
            },
            "action": dict(action),
            "observations_used": [obs.brief() for obs in used_obs],
            "unpaired_observations": [dict(item) for item in binding.unpaired],
            "dropped_previews": [dict(item) for item in binding.dropped_preview],
        },
        "tool_completed_claim": action.get("tool_status") == "completed",
        "notes": notes,
    }


def judge_case(case: Any) -> dict:
    """主入口：返回完整 verdict，或格式/绑定不合法时返回 status=rejected。

    rejected 不代表「效果失败」：它表示输入不能与这次动作同源配对，根本不产生 Verification。
    绑定阶段与计算阶段发现的结构性错误都走这条路径；合法但缺证一律走 uncertain。
    """
    origin = case.get("origin") if isinstance(case, dict) else None
    try:
        return _judge_bound(bind_case(case))
    except EvidenceBindingError as error:
        return {
            "schema_version": VERDICT_SCHEMA,
            "status": "rejected",
            "conclusion": None,
            "reason": f"输入格式/绑定不合法，拒配：{error.detail}",
            "error": error.as_dict(),
            "origin": origin,
        }
