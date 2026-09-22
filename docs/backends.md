# Adapter boundaries

Adapters implement contracts and are selected by YAML. The Agent-facing boundary is `Executor`; scene and policy details stay inside the Mock or SAPIEN adapter.

The Mock adapter is deterministic. The SAPIEN adapter owns its worker, converts declarative skill execution steps into scene commands, and optionally calls a `PolicyPort`. No benchmark-specific backend is part of the active package.
