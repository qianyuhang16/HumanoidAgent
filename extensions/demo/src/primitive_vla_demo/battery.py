"""Standalone offline demo; not a real RMbench adapter or a learned model."""

from pydantic import BaseModel, ConfigDict

from primitive_vla.agent.contracts import FinalAnswer, ModelReply
from primitive_vla.tools import FunctionTool, ToolCall, ToolDefinition, ToolResult


class BatteryOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    correct_left: bool = True
    correct_right: bool = True


class BatteryTools:
    def __init__(self, options, context):
        cfg = BatteryOptions.model_validate(options)
        self.correct = (cfg.correct_left, cfg.correct_right)
        self.orientation = [False, False]
        self.installed = False
        self.stopped = set()
        self.tools = [
            FunctionTool(ToolDefinition(name="battery.observe", description="Read the fake battery scene and simulated meter motion. Hidden polarity is not observable."), self.observe),
            FunctionTool(ToolDefinition(name="battery.insert", description="Insert two batteries in the fake scene. This does not guarantee the meter rotates.", read_only=False, resources=["battery_fixture"]), self.insert, self.stop),
            FunctionTool(ToolDefinition(name="battery.adjust", description="Flip one installed battery in the fake scene; observe the meter afterwards.", read_only=False, resources=["battery_fixture"],
                parameters={"type": "object", "properties": {"side": {"type": "string", "enum": ["left", "right"]}}, "required": ["side"], "additionalProperties": False}), self.adjust, self.stop),
        ]

    async def observe(self, args, call):
        return ToolResult(data={"source": "software_demo", "installed": self.installed,
                                "meter_rotating": self.installed and tuple(self.orientation) == self.correct})

    async def insert(self, args, call):
        call.check_active()
        if self.installed:
            return ToolResult(status="rejected", text="Already installed; inspect or adjust instead")
        self.installed = True
        return ToolResult(text="Insertion motion ended; observe the meter.")

    async def adjust(self, args, call):
        call.check_active()
        if not self.installed:
            return ToolResult(status="rejected", text="Batteries must be installed first")
        index = 0 if args["side"] == "left" else 1
        self.orientation[index] = not self.orientation[index]
        return ToolResult(data={"adjusted": args["side"]}, text="Adjustment ended; polarity remains hidden.")

    async def stop(self, call_id, reason):
        self.stopped.add(call_id)
        return True

    async def close(self):
        pass


class DemoModel:
    """Deterministic exploration model for offline wiring checks, not general reasoning."""

    def __init__(self, options, context):
        if options:
            raise ValueError("DemoModel accepts no options")
        self.trials = 0

    async def step(self, context, tools):
        results = [e for e in context.history if e["kind"] == "tool_result"]
        if not results or results[-1]["tool"] != "battery.observe":
            return ModelReply(calls=[ToolCall(name="battery.observe")])
        result = results[-1]
        if result["data"]["meter_rotating"]:
            return ModelReply(final=FinalAnswer(status="completed", reason="模拟仪表已转动。此结果来自离线软件演示。", evidence=[result["call_id"]]))
        if not result["data"]["installed"]:
            return ModelReply(calls=[ToolCall(name="battery.insert")])
        if self.trials >= 3:
            return ModelReply(final=FinalAnswer(status="blocked", reason="演示中的相对方向组合已试完。"))
        side = ("left", "right", "left")[self.trials]
        self.trials += 1
        return ModelReply(calls=[ToolCall(name="battery.adjust", arguments={"side": side})],
                          memory=f"已选择 {self.trials} 次调整，接下来检查实际仪表反馈。")

    async def close(self):
        pass
