"""Local RoboCasa task definitions; scene setup and predicates, never action policies."""
from robocasa.environments.kitchen.kitchen import Kitchen
from robocasa.models.fixtures import FixtureType


class TabletopGrasp(Kitchen):
    """Grasp one visible lemon and lift it eight centimetres above its initial height."""

    def _setup_kitchen_references(self):
        super()._setup_kitchen_references()
        self.counter = self.register_fixture_ref(
            "counter", {"id": FixtureType.COUNTER, "size": [0.6, 0.4]},
        )
        self.init_robot_base_ref = self.counter

    def _get_obj_cfgs(self):
        return [{"name": "obj", "obj_groups": "lemon", "graspable": True,
                 "placement": {"fixture": self.counter, "size": [0.15, 0.15],
                               "pos": [0, -1.0], "offset": [0, 0.12]}}]

    def _reset_internal(self):
        super()._reset_internal()
        self.initial_object_z = float(self.sim.data.body_xpos[self.obj_body_id["obj"]][2])

    def get_ep_meta(self):
        meta = super().get_ep_meta()
        meta["lang"] = "Grasp the lemon on the counter and lift it at least 8 cm while holding it."
        return meta

    def _check_success(self):
        self.lift_height = float(self.sim.data.body_xpos[self.obj_body_id["obj"]][2]) - self.initial_object_z
        self.grasp_contact = self._check_grasp(self.robots[0].gripper, self.objects["obj"])
        return self.grasp_contact and self.lift_height >= 0.08
