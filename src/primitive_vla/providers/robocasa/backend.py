"""RPent's native environment and primitives, owned by one EGL worker thread."""
from io import BytesIO
import os
import time

from primitive_vla.contracts import Failure, ToolError, new_id
from primitive_vla.tools.state.views import ViewFrame


class MotionCancelled(Exception):
    """Raised between synchronous simulator steps; no background actuator exists."""


class NativeScene:
    def __init__(self, config, publish, preview):
        os.environ["MUJOCO_GL"] = "egl"
        os.environ["MUJOCO_EGL_DEVICE_ID"] = str(config.gpu)
        if config.assets_path is not None:
            os.environ["ROBOCASA_ASSETS_PATH"] = str(config.assets_path.resolve())
        from robots.robocasa.env_client import RoboCasaEnvClient
        from robots.robocasa.env_server import RoboCasaEnvFacade
        from robots.robocasa.primitives import RoboCasaPrimitives
        from rpent.robots.components.env_client_base import BaseEnvClient

        self.cancel_event = None
        self.steps = 0
        self.publish = publish
        self.image_size = min(config.image_size, 512)
        self.facade = RoboCasaEnvFacade(
            task_name=config.task, split=config.split, seed=config.seed,
            camera_h=config.image_size, camera_w=config.image_size,
        )
        scene = self

        class LocalRpc:
            def call(self, method, args=(), kwargs=None, timeout_s=None):
                return getattr(scene.facade, method.removeprefix("env."))(
                    *args, **(kwargs or {})
                )

        class ObservedClient(RoboCasaEnvClient):
            def __init__(self):
                # Primitives initializes the episode once. Do not reset once per client as well.
                BaseEnvClient.__init__(self, LocalRpc(),
                                       expected_meta=scene.facade.get_env_meta(),
                                       reset_on_connect=False)
                self.camera_h = self.camera_w = config.image_size

            def step(self, action):
                scene.check_cancelled()
                result = super().step(action)
                scene.steps += 1
                scene.publish(scene.observation())
                preview(scene)
                return result

        try:
            self.env = ObservedClient()
            self.primitives = RoboCasaPrimitives(
                env_client=self.env, workdir=str(config.workdir), hi_res=0,
                vla_client=None, check_cancelled=self.check_cancelled, allow_reset=False,
            )
            self.language = self.env.get_task_language()
            self.publish(self.observation())
        except Exception:
            # Lifecycle boundary: release the EGL environment if initialization fails.
            self.facade.env.close()
            raise

    def check_cancelled(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise MotionCancelled()

    def observation(self):
        obs = self.env.current_raw_obs
        return {
            "observed_at": time.time(), "simulation_steps": self.steps,
            "task_language": self.language,
            "robot_state": {key: obs[key].tolist() for key in (
                "robot0_base_pos", "robot0_base_quat", "robot0_eef_pos",
                "robot0_eef_quat", "robot0_gripper_qpos",
            )},
        }

    def declarations(self):
        # This is a capability allowlist, not a task sequence. Never expose reset/finish.
        names = ("move_to", "move_delta", "rotate_pitch", "set_gripper", "release",
                 "scripted_grasp", "move_base", "navigate_to")
        declarations = []
        for name in names:
            tool = getattr(self.primitives, name)
            schema = tool.input_schema
            schema["additionalProperties"] = False
            for key, value in schema["properties"].items():
                if key in {"steps", "max_steps", "n"}:
                    value.update(minimum=1, maximum=2000)
                elif key in {"step_clip", "tol"}:
                    value["exclusiveMinimum"] = 0
                elif key in {"forward", "lateral", "turn"}:
                    value.update(minimum=-1, maximum=1)
                elif key == "gripper":
                    if value["type"] != "number":
                        del value["type"]
                        value["anyOf"] = [{"type": "number", "minimum": -1, "maximum": 1},
                                          {"const": "hold"}]
                    else:
                        value.update(minimum=-1, maximum=1)
            declarations.append((name, tool.description, schema))
        return declarations

    def invoke(self, operation, arguments, cancel_event):
        self.cancel_event = cancel_event
        self.check_cancelled()
        if operation == "capture_image":
            from PIL import Image
            import numpy as np

            camera = arguments.get("camera", "agentview")
            include_depth = arguments.get("depth", False)
            rendered = self.env.render_camera(camera, depth=include_depth)
            rgb = rendered[0] if include_depth else rendered
            image = BytesIO()
            Image.fromarray(rgb).save(image, format="PNG")
            blobs = [("rgb", image.getvalue(), "image/png")]
            if include_depth:
                depth = BytesIO()
                np.save(depth, rendered[1], allow_pickle=False)
                blobs.append(("depth", depth.getvalue(), "application/x-npy"))
            return ({**self.observation(), "camera": camera,
                     "width": rgb.shape[1], "height": rgb.shape[0],
                     "depth_unit": "metre" if include_depth else None}, blobs)
        if operation == "verify_task":
            return ({**self.observation(), "predicate": bool(self.env.check_success()),
                     "task_progress": self.env.get_task_progress(),
                     "basis": "simulator_ground_truth"}, [])
        if operation == "localize_pixel":
            if arguments["simulation_steps"] != self.steps:
                raise ToolError(Failure.PRECONDITION,
                                "camera observation is stale; capture a new image before projecting its pixels")
            camera = arguments.get("camera", "agentview")
            point = self.env.world_xyz_at(camera, arguments["row"], arguments["column"])
            return ({**self.observation(), "camera": camera, "row": arguments["row"],
                     "column": arguments["column"], "point_world": point.tolist(),
                     "coordinate_frame": "world", "unit": "metre",
                     "basis": "camera_depth_projection"}, [])
        result = getattr(self.primitives, operation)(**arguments)
        return ({"native_result": result.data, **self.observation()}, [])

    def close(self):
        # RPent's server shutdown suppresses cleanup errors; surface them to this Runtime.
        self.facade.env.close()

    def render_view(self, view_id):
        from PIL import Image

        rgb = self.env.render_camera(view_id, height=self.image_size, width=self.image_size)
        captured_at = time.time()
        image = BytesIO()
        Image.fromarray(rgb).save(image, format="JPEG", quality=80)
        return ViewFrame(new_id(), captured_at, "image/jpeg", image.getvalue(),
                         rgb.shape[1], rgb.shape[0], {"simulation_steps": self.steps})
