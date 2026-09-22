# Skill contracts

`SKILL.md` is the source of truth for a skill. YAML front matter contains identity/version, parameters, preconditions, invariants, effects, resources, retry limits, and an explicit execution declaration. Markdown below the front matter is human guidance.

```yaml
execution:
  type: program
  steps:
    - op: point
      point: tool_home
      kind: approach
```

or:

```yaml
execution:
  type: policy
  prompt: "Move the selected object to the target."
```

`program` and `policy` are mutually exclusive execution strategies. The loader validates the declaration and returns a `SkillCatalog`; the Agent never reads files or calls `load_skills`.

A candidate is a versioned skill plus concrete object/hand bindings. Preconditions are checked before execution. Effects are evidence that the injected verifier must confirm after a fresh observation.
