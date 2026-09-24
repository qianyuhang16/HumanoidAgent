"""Calibrated RGB-D geometry. All lengths are metres, poses use world coordinates."""

import numpy as np


def unproject(pixel, depth, intrinsics, world_from_camera):
    u, v = pixel
    if not np.isfinite([u, v, depth]).all() or depth <= 0:
        raise ValueError("A valid positive metric depth is required")
    ray = np.linalg.solve(np.asarray(intrinsics), [u, v, 1.0])
    return (np.asarray(world_from_camera) @ np.r_[ray * depth, 1.0])[:3]


def grasp_pose(pixel, angle, width_px, depth, intrinsics, world_from_camera, inset=.018):
    center = unproject(pixel, depth, intrinsics, world_from_camera)
    delta = np.array([np.cos(angle), -np.sin(angle)]) * width_px / 2
    a = unproject(np.asarray(pixel) - delta, depth, intrinsics, world_from_camera)
    b = unproject(np.asarray(pixel) + delta, depth, intrinsics, world_from_camera)
    closing = b - a
    closing[2] = 0
    width = float(np.linalg.norm(closing))
    if not .005 < width < .10:
        raise ValueError(f"Predicted grasp width is outside the gripper range: {width:.4f} m")
    closing /= width
    approach = np.array([0., 0., -1.])
    rotation = np.column_stack([np.cross(closing, approach), closing, approach])
    center[2] -= inset
    if not .012 <= center[2] <= .15:
        raise ValueError(f"Perceived grasp height is outside the tabletop range: {center[2]:.4f} m")
    return center, rotation, width


def depth_comparison(predicted, measured):
    valid = np.isfinite(predicted) & (predicted > .01) & np.isfinite(measured) & (measured > .01)
    if valid.sum() < 100:
        raise ValueError("Not enough valid depth measurements")
    error = np.abs(predicted[valid] - measured[valid])
    return {"mae_m": float(error.mean()), "median_error_m": float(np.median(error)),
            "p95_error_m": float(np.percentile(error, 95))}


def clearance(frame, center, radius=.075, floor=.08):
    """Conservative visible vertical corridor check; never reads actors or labels."""
    depth = frame["sensor_depth"]
    rows, cols = np.indices(depth.shape)
    valid = np.isfinite(depth) & (depth > .01)
    if valid.sum() < 100:
        return {"verdict": "unknown", "reason": "Insufficient measured depth"}
    pixels = np.stack([cols[valid], rows[valid], np.ones(valid.sum())])
    points = (np.linalg.inv(frame["intrinsics"]) @ pixels) * depth[valid]
    transform = frame["world_from_camera"]
    points = (transform[:3, :3] @ points + transform[:3, 3:4]).T
    region = np.all(np.abs(points[:, :2] - np.asarray(center)[:2]) < radius, axis=1)
    count = int(region.sum())
    occupied = int(np.count_nonzero(region & (points[:, 2] > floor) & (points[:, 2] < .5)))
    # A tiny patch of valid pixels is not evidence for a whole clear corridor.
    def cells(selected):
        return set(map(tuple, np.floor((points[selected, :2] - (np.asarray(center)[:2] - radius)) / (2 * radius / 6)).astype(int)))
    coverage = cells(region)
    occupied_cells = cells(region & (points[:, 2] > floor) & (points[:, 2] < .5))
    enough = count >= 100 and len(coverage) >= 30
    return {"verdict": "infeasible" if occupied >= 12 else "feasible" if enough else "unknown",
            "reason": "Visible geometry obstructs the gripper corridor" if occupied >= 12 else
                      "Visible corridor is clear" if enough else "Insufficient corridor coverage",
            "source": "measured RGB-D point cloud", "visible_points": count,
            "occupied_points": occupied, "observed_cells": len(coverage), "occupied_cells": len(occupied_cells), "total_cells": 36,
            "half_extent_xy_m": radius, "obstacle_floor_m": floor,
            "measurement_note": "observed_cells measures depth coverage, NOT obstacle occupancy; the corridor is square in XY",
            "scope": "Conservative vertical gripper corridor only; excludes full arm and unseen geometry"}
