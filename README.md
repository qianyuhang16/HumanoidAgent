# Primitive VLA

Primitive VLA is a configurable embodied-agent runtime. The runtime has one canonical `Agent` loop and explicit contracts for observation, decision making, skill catalogs, execution, task compilation, memory, and verification.

## Architecture

```text
YAML composition
  -> compiler + decision model + skill catalog + scene/executor + verifier
  -> Agent: observe -> decide -> execute -> observe -> verify -> remember
```

The Agent imports only domain contracts. Mock and SAPIEN are adapters selected by YAML. `SKILL.md` files are declarative contracts; the composition root loads them before a run. VLM providers and the optional π0.5 policy adapter are also selected through adapter paths.

Key modules:

- `primitive_vla.agent.contracts`: stable ports and messages.
- `primitive_vla.agent.core`: the canonical budgeted loop.
- `primitive_vla.runtime.composition`: the only dynamic adapter resolver.
- `primitive_vla.skills.loader`: `SKILL.md` discovery and validation.
- `primitive_vla.execution`: Agent-facing execution seam.
- `primitive_vla.verification`: independent task/effect verification.
- `primitive_vla.web.app`: the supported web console.

## Run

```bash
PYTHONPATH=src python -m primitive_vla init-config --workspace workspace
PYTHONPATH=src python -m primitive_vla run \
  --config configs/sapien.yaml \
  --workspace workspace/sapien \
  --instruction "把所有配料放入碗里，然后搅拌并放回工具"
PYTHONPATH=src python -m primitive_vla serve \
  --config configs/sapien.yaml \
  --workspace workspace/sapien
```

`config.yaml` is the canonical persisted configuration format. Run artifacts remain JSON/JSONL for interoperability.

## Configuration

A composition document names every replaceable adapter:

```yaml
adapters:
  agent: primitive_vla.agent.core:Agent
  compiler: primitive_vla.agent.compiler:ConfiguredTaskCompiler
  decision_model: primitive_vla.agent.decision:RuleDecisionModel
  skill_loader: primitive_vla.skills.loader:load_catalog
  executor: primitive_vla.execution.adapter:ExecutiveExecutor
  verifier: primitive_vla.verification.rules:RuleVerifier
  vlm: primitive_vla.providers.factory:create_client
  policy: primitive_vla.vla.client:PolicyClient

skills:
  - builtin:sapien
scene: ...
agent:
  max_steps: 24
  timeout_s: 1200
```

See [architecture](docs/architecture.md), [configuration](docs/configuration.md), and [skills](docs/skills.md).
