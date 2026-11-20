"""Pure business policy proposals only. No tool, motion, training or Router integration."""

from copy import deepcopy

POLICY_VERSION = "t05_obstacle_recovery_candidate_v2_scoped_authorization"
MOVEMENT_REQUIREMENTS = {
    "light_object": True, "protected_object": False,
    "placement_available": True, "action_conditions_available": True,
}
AUTHORIZATION_KEYS = {
    "kind", "granted", "task_id", "scene_version", "obstacle_id", "target_region_id",
}


def fact(context, key):
    """Scope provenance before using an explicitly supplied value; no NLP inference.

    trusted_sources must be populated by a future authenticated host, not by a model.
    This pure fixture function does not authenticate a source or prove a physical fact.
    """
    row = context.get("facts", {}).get(key)
    if not isinstance(row, dict) or type(row.get("value")) is not bool:
        return None
    if (not row.get("evidence_refs") or row.get("source") == "user_reply"
            or row.get("source") not in context.get("trusted_sources", ())
            or row.get("scene_version") != context.get("scene_version")
            or row.get("object_id") != context.get("obstacle_id")
            or type(row.get("synthetic")) is not bool
            or row["synthetic"] != context.get("synthetic")):
        return None
    if key == "placement_available" and (
            not context.get("proposed_target_region_id")
            or row.get("target_region_id") != context["proposed_target_region_id"]):
        return None
    return row["value"]


def movement_authorization(context):
    row = context.get("user_authorization")
    if row is None:
        return "missing", None
    if (not isinstance(row, dict) or set(row) != AUTHORIZATION_KEYS
            or row.get("kind") != "permission_to_move"
            or type(row.get("granted")) is not bool):
        return "invalid_shape_or_extra_claims", None
    for field, context_field in (
            ("task_id", "task_id"), ("scene_version", "scene_version"),
            ("obstacle_id", "obstacle_id"),
            ("target_region_id", "proposed_target_region_id")):
        expected = context.get(context_field)
        if not isinstance(expected, str) or not expected or row.get(field) != expected:
            return "scope_mismatch", None
    return ("granted" if row["granted"] else "declined"), row["granted"]


def propose_recovery(context, task_projection):
    result = {
        "policy_version": POLICY_VERSION, "status": "candidate",
        "router_integration": "pending", "physical_execution_authorized": False,
        "original_task_projection": deepcopy(task_projection),
        "changes_original_goal_status": False, "temporary_goal": None,
        "counts_as_original_placement": False,
        "authorization_status": "not_evaluated",
        "decision": "blocked", "missing": [], "required_after_movement": [
            "sourced movement Verification for the temporary goal",
            "new scoped observation of the passage after that Verification",
            "resume remaining original goals; retain completed goals and constraints",
        ],
    }
    if context.get("stop_confirmed") is not True or context.get("interface_fault"):
        result["reason"] = "unconfirmed_stop_or_interface_fault_requires_host_resolution"
        return result
    detour = fact(context, "detour_available")
    providers = context.get("providers", {})
    if detour is True:
        result["decision"] = ("propose_existing_detour" if providers.get("navigate") is True
                              else "blocked")
        result["reason"] = "prefer_existing_navigation; no movement proposed"
        return result
    if detour is None:
        result.update(decision="observe", reason="detour availability is unknown")
        return result
    missing = [key for key, expected in MOVEMENT_REQUIREMENTS.items()
               if fact(context, key) is None]
    contrary = [key for key, expected in MOVEMENT_REQUIREMENTS.items()
                if fact(context, key) is not None and fact(context, key) != expected]
    if contrary:
        result.update(reason="movement requirement explicitly not met", missing=contrary)
        return result
    if missing:
        result.update(decision="observe", reason="missing_sourced_facts",
                      missing=missing)
        return result
    authorization_status, granted = movement_authorization(context)
    result["authorization_status"] = authorization_status
    if granted is None:
        scope = {
            "kind": "permission_to_move", "task_id": context.get("task_id"),
            "scene_version": context.get("scene_version"),
            "obstacle_id": context.get("obstacle_id"),
            "target_region_id": context.get("proposed_target_region_id"),
        }
        result.update(
            decision="ask_user", reason="scoped_permission_required",
            missing=["permission_to_move"], question_scope=scope,
            question="May obstacle {obstacle} be moved to region {region} for task {task} "
                     "in scene {scene}? This grants permission only.".format(
                         obstacle=scope["obstacle_id"], region=scope["target_region_id"],
                         task=scope["task_id"], scene=scope["scene_version"]),
            reply_is_not_spatial_or_effect_evidence=True,
        )
        return result
    if granted is False:
        result["reason"] = "user_declined_movement"
        return result
    if providers.get("move_obstacle") is not True:
        result["reason"] = "expert movement Provider absent"
        return result
    result.update(decision="propose_temporary_movement_goal",
                  reason="all declared conditions present; still only a business proposal",
                  temporary_goal={
                      "kind": "obstacle_displaced",
                      "object_id": context["obstacle_id"],
                       "task_id": context["task_id"],
                       "target_region_id": context["proposed_target_region_id"],
                      "scene_version": context["scene_version"],
                      "counts_as_original_placement": False,
                  })
    return result
