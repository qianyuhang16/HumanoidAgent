"""Task-specific measured facts and IK targets; no high-level action sequences."""


class CoverBlocks:
    name = 'cover_blocks'
    positions = ('left', 'middle', 'right')
    stage_max = 6

    def failed(self, env):
        return bool(env.fail_flag)

    def stage(self, env):
        return env.current_state_pointer

    def facts(self, env):
        stage = self.stage(env)
        values = {}
        for index, position in enumerate(self.positions):
            values[f'covered:{position}'] = stage >= index + 1
            values[f'cover_on:{position}'] = bool(env.check_if_cover(index))
        for index, color in enumerate(('red', 'green', 'blue')):
            values[f'uncovered:{color}'] = stage >= index + 4
        return values

    def targets(self, env, position):
        cover = env.covers[self.positions.index(position)]
        return [(cover, 'left' if cover.get_pose().p[0] < 0 else 'right', {})]


class BatteryTry:
    name = 'battery_try'
    stage_max = 3  # two measured insertions + native full success (diagnostic only)

    def __init__(self):
        self.initialized = False

    def failed(self, env):
        return False  # This upstream task has no cover_blocks-style fail_flag.

    def facts(self, env):
        values = {}
        for side, actor, target in [('left', env.battery1, env.target_pose1_p),
                                    ('right', env.battery2, env.target_pose2_p)]:
            values[f'seated:{side}'] = bool(env.check_battery_in_slot(actor, target))
            polarity = env.get_battery_state(actor)
            for index, direction in enumerate(('positive', 'negative')):
                values[f'polarity:{side}:{direction}'] = polarity == index
        self.initialized |= values['seated:left'] and values['seated:right']
        values['initialized'] = self.initialized
        values['dashboard:on'] = bool(env.check_dashboard_on())
        # Never read correct_combination here: only measured poses and instrument state.
        return values

    def stage(self, env):
        values = self.facts(env)
        return int(values['seated:left']) + int(values['seated:right']) + int(env.eval_success)

    def targets(self, env, position):
        targets = {'left': (env.battery1, 'left', {'grasp_dis': 0.03}),
                   'right': (env.battery2, 'right', {'grasp_dis': 0.03})}
        return list(targets.values()) if position == 'pair' else [targets[position]]


def task_adapter(name):
    return {'cover_blocks': CoverBlocks, 'battery_try': BatteryTry}[name]()
