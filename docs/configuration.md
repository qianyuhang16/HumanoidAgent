# Configuration

YAML is the canonical configuration format. `primitive_vla init-config` writes `config.yaml`; the main CLI and web console read the same document.

## Composition

```yaml
adapters:
  backends:
    mock: primitive_vla.backends.factories:mock
    sapien: primitive_vla.backends.factories:sapien
  agent: primitive_vla.agent.core:Agent
  compiler: primitive_vla.agent.compiler:ConfiguredTaskCompiler
  decision_model: primitive_vla.agent.decision:RuleDecisionModel
  executor: primitive_vla.execution.adapter:ExecutiveExecutor
  verifier: primitive_vla.verification.rules:RuleVerifier
  observer: configured
  skill_loader: primitive_vla.skills.loader:load_catalog
  vlm: primitive_vla.providers.factory:create_client
  policy: null

skill_sources:
  - builtin:kitchen

backends:
  mock:
    type: mock
observer_backend: mock
```

Use exactly one scene backend for a task. SAPIEN requires its simulation block and a matching symbolic scene. Replace `decision_model`, `compiler`, `skill_loader`, `verifier`, or `vlm` by changing only the adapter path; the Agent remains unchanged.

The full SAPIEN example is [`configs/sapien.yaml`](../configs/sapien.yaml). HTTP and JSON composition files from the retired runtime are intentionally gone.
