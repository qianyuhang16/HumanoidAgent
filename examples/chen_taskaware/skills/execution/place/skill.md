---
{
  "id": "execution.chen.place",
  "version": "1.0",
  "description": "Move a synthetic item into the synthetic box; no effect conclusion.",
  "tool": {
    "domain": "execution",
    "provider": "synthetic.chen_taskaware",
    "input_schema": {
      "type": "object",
      "properties": {
        "external_task_id": {
          "type": "string",
          "minLength": 1
        },
        "scene_revision": {
          "type": "string",
          "minLength": 1
        },
        "goal_id": {
          "type": "string",
          "minLength": 1
        },
        "visit": {
          "type": "integer",
          "minimum": 1
        },
        "attempt": {
          "type": "integer",
          "minimum": 1
        }
      },
      "required": [
        "external_task_id",
        "scene_revision",
        "goal_id",
        "visit",
        "attempt"
      ],
      "additionalProperties": false
    },
    "output_schema": {
      "type": "object",
      "properties": {
        "identity": {
          "type": "object"
        },
        "synthetic": {
          "const": true
        }
      },
      "required": [
        "identity",
        "synthetic"
      ]
    },
    "supported_robots": [
      "chen.synthetic"
    ],
    "required_resources": [
      "synthetic_world"
    ],
    "execution_mode": "sync",
    "timeout": 10,
    "entrypoint": "runtime.synthetic:factory",
    "options": {
      "operation": "place"
    }
  }
}
---
# place

Move a synthetic item into the synthetic box; no effect conclusion.

This capability is synthetic only. Read the current task_scene context before calling.
Use its external identity, scene revision, goal, visit and attempt unchanged.
Only independent Verification can establish an effect; completed execution cannot.
A failed or uncertain result must remain unresolved. Do not replay verified actions.
Choose actions from current evidence; this document does not prescribe an action sequence.
Do not infer physical coordinates, reachability, grasp safety or stopping from this fixture.
