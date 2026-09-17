---
name: DECLARE_COMPLETE
version: '1'
parameters: {}
preconditions:
  inspection:needed: 'false'
  visual:complete: 'true'
invariants: {}
effects:
  agent:complete: 'true'
resources: []
backend: grounded_sam
program: [{op: declare_complete}]
timeout_s: 5
max_attempts: 1
description: Commit an Agent completion claim based on fresh visual evidence. This does not query the simulator evaluator.
---

# Declare visual completion

Only call when visible evidence supports both inserted batteries and an activated
instrument. This records the Agent's claim, which can be wrong. The independent
native evaluator runs after the episode, and its verdict cannot trigger retries.
