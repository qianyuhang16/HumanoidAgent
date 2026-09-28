"""Small configurable fake robot for exercising tools without hardware or a VLA."""

from copy import deepcopy

from pydantic import BaseModel, ConfigDict, Field

from primitive_vla.tools import FunctionTool, ToolDefinition, ToolResult


class MockOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    arms: list[str] = Field(default_factory=lambda: ["arm_a"], min_length=1)
    objects: dict[str, list[float]] = Field(default_factory=lambda: {"sample": [0.4, 0.1, 0.2]})


class MockRobot:
    """No physical accuracy claim: this provider only records requested fake state."""

    def __init__(self, options, context):
        cfg = MockOptions.model_validate(options)
        self.objects = cfg.objects
        self.arms = {arm: {"position": [0., 0., 0.], "gripper": "open"} for arm in cfg.arms}
        self.stopped = set()
        self.tools = [FunctionTool(ToolDefinition(name="robot.observe",
            description="Observe the mock robot and object positions. This is a software fake, not physical evidence."), self.observe)]
        for arm in cfg.arms:
            async def move(args, call, arm=arm):
                call.check_active()
                if call.call_id in self.stopped:
                    return ToolResult(status="cancelled", text="Call was stopped")
                self.arms[arm]["position"] = list(args["position"])
                return ToolResult(data={"arm": arm, "position": args["position"]}, text="Mock motion ended; inspect the scene for effects.")

            async def grip(args, call, arm=arm):
                call.check_active()
                if call.call_id in self.stopped:
                    return ToolResult(status="cancelled", text="Call was stopped")
                self.arms[arm]["gripper"] = args["state"]
                return ToolResult(data={"arm": arm, "gripper": args["state"]}, text="Mock gripper command ended; this does not certify a grasp.")

            self.tools.extend([
                FunctionTool(ToolDefinition(name=f"robot.{arm}.move", description=f"Move mock {arm} to a position in metres in its base frame.",
                    parameters={"type": "object", "properties": {"position": {"type": "array", "items": {"type": "number", "minimum": -2, "maximum": 2}, "minItems": 3, "maxItems": 3}},
                                "required": ["position"], "additionalProperties": False}, read_only=False, resources=[f"robot:{arm}"]), move, self.stop),
                FunctionTool(ToolDefinition(name=f"robot.{arm}.gripper", description=f"Open or close mock {arm}'s gripper.",
                    parameters={"type": "object", "properties": {"state": {"enum": ["open", "closed"]}},
                                "required": ["state"], "additionalProperties": False}, read_only=False, resources=[f"robot:{arm}"]), grip, self.stop),
            ])

    async def observe(self, args, call):
        return ToolResult(data=deepcopy({"source": "mock", "arms": self.arms, "objects": self.objects}))

    async def stop(self, call_id, reason):
        self.stopped.add(call_id)
        return True

    async def close(self):
        pass
