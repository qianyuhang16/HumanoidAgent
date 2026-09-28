"""Calibrated spatial evidence for model-directed arm choice; never moves arms."""
import numpy as np

from ..ik.pixel import compensated_target, rigid_transform


def arm_context(ik, pixel, size, arms, distance_margin_m):
    candidates = {}
    for arm in arms:
        try:
            _, handeye, manifest = ik.calibration_result(arm, 'handeye')
            if handeye.get('camera') != 'front':
                raise ValueError('Expected front-camera calibration')
            point, target = compensated_target(
                pixel, size, manifest['intrinsics'], rigid_transform(handeye['T_base_from_camera']),
                ik.options.target_base_z_m, ik.options.forward_compensation_m[arm])
            candidates[arm] = {
                'point_in_base_m': point.round(4).tolist(),
                'hover_target_in_base_m': target.round(4).tolist(),
                'object_horizontal_distance_m': float(np.linalg.norm(point[:2])),
                'hover_horizontal_distance_m': float(np.linalg.norm(target[:2])),
            }
        except (OSError, KeyError, ValueError) as error:
            candidates[arm] = {'error': str(error)}
    ranked = sorted((a for a in arms if 'error' not in candidates[a]),
                    key=lambda a: candidates[a]['object_horizontal_distance_m'])
    preferred = None
    if ranked and len(ranked) == len(arms):
        if len(ranked) == 1 or (candidates[ranked[1]]['object_horizontal_distance_m']
                               - candidates[ranked[0]]['object_horizontal_distance_m'] > distance_margin_m):
            preferred = ranked[0]
    return {
        'preferred_arm': preferred, 'candidates': candidates,
        'distance_margin_m': distance_margin_m, 'reachability_checked': False,
        'basis': 'Calibrated RGB ray/base-height plane; prefer the nearer same-side arm. '
                 'Distances are estimates, not reachability or collision checks. '
                 'A local IK failure alone does not justify reaching across with the other arm.',
    }
