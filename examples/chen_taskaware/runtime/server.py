"""Platform process entry; stdout belongs exclusively to the framework protocol."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from primitive_vla.memory import Memory
from primitive_vla.runtime.connections.server import PlatformRuntime, serve
from primitive_vla.tools.assembly import PlatformPackage

from .synthetic import Environment


@asynccontextmanager
async def connect(params):
    package = PlatformPackage(Path(__file__).resolve().parents[1])
    profile, skills, options = package.assemble(params["profile_id"], params["options"])
    if options.pop("acknowledge_synthetic", False) is not True:
        raise ValueError("explicit acknowledge_synthetic is required")
    if options.pop("synthetic") is not True:
        raise ValueError("no production provider is supplied")
    if params["unfinished_calls"]:
        raise ValueError("unfinished calls quarantine this platform; no automatic replay")
    scenario = options.pop("synthetic_scenario", "positive")
    if set(options) != {"task"}:
        raise ValueError("only the validated task and a synthetic scenario are accepted")
    memory = Memory(Path(params["workdir"]) / "platform-state")
    environment = Environment(memory, options["task"], scenario)
    try:
        yield PlatformRuntime(
            "chen-synthetic", "chen.synthetic", "Chen synthetic scene (not a robot)",
            {"synthetic_world"}, memory.read_resource, skills, package.catalog(), profile.id,
            context=environment,
        )
    finally:
        environment.close()
        memory.close()


if __name__ == "__main__":
    asyncio.run(serve(connect))
