"""Select a reachable free-space entry pose without executing manipulation."""


def select_approach(env, actor, arm, distance, grasp_distance=0., fallback=None):
    """Prefer annotated poses; optionally recover from rotated surface annotations.

    The fallback uses measured object position and configured tool orientation.
    It plans only an approach, never closes the gripper or moves an object.
    All candidates are checked from the actual current robot configuration.
    """
    attempts = []
    distances = [distance, *(fallback or {}).get('distances', [])]
    for clearance in dict.fromkeys(distances):
        pose, _ = env.choose_grasp_pose(actor, arm_tag=arm, pre_dis=clearance,
                                        target_dis=grasp_distance)
        attempts.append({'source': 'contact_annotation', 'clearance': clearance,
                         'reachable': pose is not None})
        if pose is not None:
            return pose, attempts
    if fallback:
        plan = getattr(env.robot, f'{arm}_plan_path')
        position = actor.get_pose().p
        for clearance in fallback['distances']:
            for quaternion in fallback['orientations'][str(arm)]:
                pose = [float(position[0]), float(position[1]),
                        float(position[2]) + fallback['tool_offset'] + clearance, *quaternion]
                reachable = plan(pose)['status'] == 'Success'
                attempts.append({'source': 'world_vertical', 'clearance': clearance,
                                 'target_pose': pose, 'reachable': reachable})
                if reachable:
                    return pose, attempts
    return None, attempts
