"""Interactive executor sandbox: retain weights while rebuilding the simulator."""
from .rmbench_sensors import SensorWorld


class VLALabWorld(SensorWorld):
    def warmup(self):
        first_load = self.policy is None
        metadata = super().warmup()
        if first_load:
            self.policy.seed(self.config['seed'])
        return metadata

    def reset_scene(self, seed):
        if self.active:
            raise RuntimeError('Stop the active command before resetting')
        policy = self.policy
        policy.reset()
        config = dict(self.config, seed=seed)
        directory = self.directory.parent / f"scene-{self.scene_index + 1:03d}"
        index = self.scene_index + 1
        self.close()
        # New simulator/cameras/trajectory; the loaded executor remains the same object.
        self.__init__(config, directory)
        self.policy, self.scene_index, self.finalized = policy, index, False
        self.policy.seed(seed)
        return self.session_info()

    scene_index = 0

    def session_info(self):
        return {'directory':str(self.directory), 'scene_index':self.scene_index,
                'seed':self.config['seed'], 'model_identity':id(self.policy.executor) if self.policy else None,
                'observation':self.observe()}

    def diagnostics(self):
        if self.active:
            raise RuntimeError('Diagnostics are available only after control stops')
        result = {}
        for side, actor, target in [('left',self.env.battery1,self.env.target_pose1_p),
                                    ('right',self.env.battery2,self.env.target_pose2_p)]:
            delta=actor.get_pose().p-target
            result[side]={'in_slot_xy':bool(self.env.check_battery_in_slot(actor,target)),
                          'orientation':'positive' if self.env.get_battery_state(actor)==0 else 'negative',
                          'position_error_xyz':delta.tolist()}
        return {'batteries':result,'instrument_on':bool(self.env.check_dashboard_on()),
                'source':'simulator diagnostics; display only, never fed to policy',
                'criterion':'in_slot_xy checks XY distance < 0.02 m; not a full insertion/release test'}
