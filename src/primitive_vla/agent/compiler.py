"""Optional legacy task compiler; not used by the model-directed Agent runtime."""

from primitive_vla.agent.task_parser import TaskParser
from primitive_vla.domain.contracts import CompileContext


class ConfiguredTaskCompiler:
    def __init__(self, parser: TaskParser, **_):
        self.parser = parser

    async def compile(self, instruction: str, context: CompileContext):
        return await self.parser.parse(instruction, context.task_data, context.state, list(context.skills))
