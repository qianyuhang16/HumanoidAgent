"""具体空间命题的判别计算：甲是否位于开放盒内部空腔。

这是本模块「不是字段校验器」的核心：真的做几何计算，并明确区分
「稳稳在里面」「明确在外面」「压在边界/精度不足，不可判」。

判据（详见 03_方法与判据.md，R1 修订后为「单一膨胀体双向判据」）：
  pad      = 位置测量精度 sigma + 任务容差带 boundary_band_m
  膨胀体   物体按 (自身尺寸 + pad) 向外膨胀后的轴对齐盒
  支持      膨胀体仍完整落在空腔内，且最小余量 >= inside_margin_m
  反证      膨胀体与空腔在任一轴上完全不相交，且分离间距 >= inside_margin_m
             （是否允许据此下 failure，还要由 judge 的覆盖度门控决定）
  不可判    其余（部分交叠、骑在盒沿、擦边、精度不足、材料缺失）

关键性质：pad 越大，膨胀体越大，「完整在内」和「完全不交」都越难成立，
结论只会退化成 uncertain，绝不会把盒内物体翻转成 failure。
R1 之前用「内缩」做反证，pad 超过物体半尺寸时会退化成中心点，已废弃。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Any

from .errors import EvidenceBindingError

EPS = 1e-9


@dataclass(frozen=True)
class Rect:
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    def as_dict(self) -> dict:
        return asdict(self)


def _vec(value: Any, size: int, where: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise EvidenceBindingError("format_invalid", f"{where} 必须是长度 {size} 的数值数组")
    out = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)):
            raise EvidenceBindingError("format_invalid", f"{where} 含非有限数值")
        out.append(float(item))
    return out


def _rect(value: Any, where: str) -> Rect:
    if not isinstance(value, dict) or "min" not in value or "max" not in value:
        raise EvidenceBindingError("format_invalid", f"{where} 必须是 {{min:[x,y], max:[x,y]}}")
    lo = _vec(value["min"], 2, f"{where}.min")
    hi = _vec(value["max"], 2, f"{where}.max")
    if lo[0] >= hi[0] or lo[1] >= hi[1]:
        raise EvidenceBindingError("format_invalid", f"{where} 的 min 必须严格小于 max")
    return Rect(lo[0], lo[1], hi[0], hi[1])


def validate_cavity(cavity: dict) -> tuple[bool, str, dict]:
    """空腔证据资格：必须直接测量，且严格小于完整外框，绝不能拿 AABB 冒充。"""
    detail: dict[str, Any] = {}
    measurement = cavity.get("cavity_measurement")
    method = cavity.get("method")
    detail["cavity_measurement"] = measurement
    detail["method"] = method
    if measurement != "direct":
        return False, "盒内空腔未直接测量；完整 AABB / 类别猜测不能证明空腔或物品入盒", detail
    if not isinstance(method, str) or not method.strip():
        return False, "空腔直接测量缺少 method（来源方法）说明", detail
    accuracy = cavity.get("accuracy_m")
    if isinstance(accuracy,(int,float)) and not isinstance(accuracy,bool) and not math.isfinite(float(accuracy)):
        raise EvidenceBindingError('format_invalid','cavity.accuracy_m must be finite')
    if isinstance(accuracy, bool) or not isinstance(accuracy, (int, float)) or accuracy < 0:
        return False, "空腔测量缺少 accuracy_m（测量精度），无法评估判据适用性", detail
    detail["accuracy_m"] = float(accuracy)

    footprint = _rect(cavity.get("footprint"), "cavity.footprint")
    z_range = _vec(cavity.get("z_range"), 2, "cavity.z_range")
    if z_range[0] >= z_range[1]:
        raise EvidenceBindingError("format_invalid", "cavity.z_range 必须 [内底, 沿口] 且严格递增")
    detail["footprint"] = footprint.as_dict()
    detail["z_range"] = z_range

    bbox = cavity.get("full_bbox")
    if bbox is not None:
        blo = _vec(bbox.get("min"), 3, "cavity.full_bbox.min")
        bhi = _vec(bbox.get("max"), 3, "cavity.full_bbox.max")
        outer = Rect(blo[0], blo[1], bhi[0], bhi[1])
        detail["full_bbox"] = {"min": blo, "max": bhi}
        strictly_inside = (footprint.min_x > outer.min_x + EPS and footprint.min_y > outer.min_y + EPS
                           and footprint.max_x < outer.max_x - EPS and footprint.max_y < outer.max_y - EPS
                           and z_range[0] > blo[2] + EPS and z_range[1] < bhi[2] - EPS)
        if not strictly_inside:
            return False, "空腔范围与完整外框重合或越界：不能由完整 AABB 推断盒内空腔", detail
        detail["wall_thickness_inferred_m"] = [
            round(footprint.min_x - outer.min_x, 6),
            round(outer.max_x - footprint.max_x, 6),
            round(z_range[0] - blo[2], 6),
        ]
    else:
        walls = cavity.get("wall_thickness_evidence")
        if not isinstance(walls, list) or not walls:
            return False, "未提供完整外框用于一致性核对，也没有壁厚/内壁直接证据，空腔资格不足", detail
        detail["wall_thickness_evidence"] = walls
    return True, "空腔为直接测量且严格位于完整外框内部", detail


@dataclass(frozen=True)
class GeometricVerdict:
    state: str  # inside | outside | boundary | precision_insufficient
    metrics: dict
    reason: str


def evaluate_containment(cavity: dict, pose: dict, tolerance: dict) -> GeometricVerdict:
    """对「物体是否位于空腔内」做实际计算。"""
    footprint = _rect(cavity.get("footprint"), "cavity.footprint")
    z_min, z_max = _vec(cavity.get("z_range"), 2, "cavity.z_range")
    center = _vec(pose.get("center"), 3, "object_pose.center")
    half = _vec(pose.get("half_extent"), 3, "object_pose.half_extent")
    if any(h < 0 for h in half):
        raise EvidenceBindingError("format_invalid", "object_pose.half_extent 不得为负")
    sigma = pose.get("accuracy_m")
    band = float(tolerance["boundary_band_m"])
    margin = float(tolerance["inside_margin_m"])

    metrics: dict[str, Any] = {
        "center": center,
        "half_extent": half,
        "cavity_footprint": footprint.as_dict(),
        "cavity_z_range": [z_min, z_max],
        "boundary_band_m": band,
        "inside_margin_m": margin,
        "position_accuracy_m": sigma,
    }
    if (isinstance(sigma, bool) or not isinstance(sigma, (int, float))
            or not math.isfinite(float(sigma)) or sigma < 0):
        return GeometricVerdict("precision_insufficient", metrics,
                                "物体位置缺少合法的测量精度 accuracy_m，无法评估判据适用域")
    sigma = float(sigma)
    # Local conservative correction: cavity measurement error is not exact geometry.
    cavity_sigma=float(cavity['accuracy_m'])
    sigma += cavity_sigma
    metrics['cavity_accuracy_m']=cavity_sigma
    metrics['combined_accuracy_m']=sigma
    if sigma > band / 2:
        metrics["precision_rule"] = "2*sigma <= boundary_band_m"
        return GeometricVerdict(
            "precision_insufficient", metrics,
            f"位置精度不足：2σ={2 * sigma:.4f} m > 容差带 {band:.4f} m，按声明规则不可判",
        )

    pad = sigma + band
    cx, cy, cz = center
    hx, hy, hz = half
    inflated = Rect(cx - hx - pad, cy - hy - pad, cx + hx + pad, cy + hy + pad)
    z_bottom, z_top = cz - hz - pad, cz + hz + pad
    clearance = min(inflated.min_x - footprint.min_x, footprint.max_x - inflated.max_x,
                    inflated.min_y - footprint.min_y, footprint.max_y - inflated.max_y,
                    z_bottom - z_min, z_max - z_top)

    # 反证也用同一个「膨胀体」：只要它在任一轴上与空腔完全不重叠，就能排除物体在空腔内。
    # 不再用「内缩」：当 pad > 物体半尺寸时内缩会退化成一个点，failure 会只由中心点决定、
    # 丢失物体体积约束（R1 修订修复的缺陷，由「容差带过大」的提问暴露）。
    # 对轴对齐盒，任一轴分离即两盒不相交，因此取各轴分离量的最大值。
    separation_axes = (
        inflated.min_x - footprint.max_x,   # 整体在右侧，与空腔不相交
        footprint.min_x - inflated.max_x,   # 整体在左侧
        inflated.min_y - footprint.max_y,
        footprint.min_y - inflated.max_y,
        z_bottom - z_max,                  # 整体高于沿口
        z_min - z_top,                     # 整体低于内底
    )
    separation = max(separation_axes)

    metrics.update({
        "pad_m": round(pad, 6),
        "clearance_m": round(clearance, 6),
        "separation_m": round(separation, 6),
        "outside_gap_m": round(separation, 6),  # 兼容旧字段名，语义已改为膨胀体分离量
        "inflated_footprint": inflated.as_dict(),
        "inflated_z": [round(z_bottom, 6), round(z_top, 6)],
        "precision_rule": "2*sigma <= boundary_band_m",
        "criterion": "single inflated box: fully inside -> inside; fully disjoint -> outside; else boundary",
        "inflation_vs_object": {
            "pad_m": round(pad, 6),
            "min_half_extent_m": round(min(hx, hy, hz), 6),
            "pad_exceeds_object_half_extent": pad > min(hx, hy, hz),
        },
    })

    if clearance >= margin:
        return GeometricVerdict("inside", metrics,
                                f"按 {pad:.4f} m 膨胀后仍完整位于空腔内，最小余量 {clearance:.4f} m ≥ {margin:.4f} m")
    if separation >= margin:
        return GeometricVerdict("outside", metrics,
                                f"按 {pad:.4f} m 膨胀后与空腔完全不相交，分离间距 {separation:.4f} m ≥ {margin:.4f} m")
    return GeometricVerdict(
        "boundary", metrics,
        f"膨胀后既未完整落在空腔内（余量 {clearance:.4f} m），也未与空腔完全分离（间距 "
        f"{separation:.4f} m），均未达 {margin:.4f} m 门槛；证据合法但不可判",
    )


def poses_conflict(poses: list[dict], pad: float) -> tuple[bool, float]:
    """多条同源物体位姿是否互相矛盾（超过 2*pad 视为冲突）。"""
    centers = [_vec(p.get("center"), 3, "object_pose.center") for p in poses]
    worst = 0.0
    for i in range(len(centers)):
        for j in range(i + 1, len(centers)):
            dist = math.dist(centers[i], centers[j])
            worst = max(worst, dist)
    return worst > 2 * pad, round(worst, 6)
