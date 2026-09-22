# Simulation adapters

The retained scene families are `mock` and `sapien`.

The Mock adapter is deterministic and useful for contract tests and CLI smoke tests. SAPIEN owns the physical worker and observation stream. Both are selected through `adapters.backends` and `backends` in YAML.

SAPIEN skills use either a declarative motion program or a policy execution declaration. Optional π0.5 inference is injected as a policy adapter and is hidden behind the executor seam.
