"""Task compiler adapters for the canonical Agent runtime."""

from primitive_vla.agent.task_parser import TaskParser
from primitive_vla.agent.contracts import CompileContext


class ConfiguredTaskCompiler:
    def __init__(self, parser: TaskParser, **_):
        self.parser = parser

    async def compile(self, instruction: str, context: CompileContext):
        return await self.parser.parse(instruction, context.task_data, context.state, list(context.skills))
