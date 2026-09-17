---
name: INSPECT_SCENE
version: '1'
parameters: {}
preconditions:
  inspection:needed: 'true'
invariants: {}
effects:
  inspection:needed: 'false'
resources: [perception]
backend: grounded_sam
program: [{op: inspect}]
timeout_s: 240
max_attempts: 12
description: Run Grounding DINO and SAM on a fresh RGB-D snapshot, then visually assess task predicates. Unknowns are valid outputs.
---

# Grounded RGB-D perception plugin

Call before the first movement and after every manipulation. This is an executable
Agent skill, not a simulator fact query. The backend uses configured local weights,
text queries and thresholds; no object actor IDs, segmentation labels or poses.

1. Grounding DINO returns text-conditioned boxes and scores.
2. SAM returns box-conditioned foreground masks. Its confidence is not task success.
3. Calibrated depth converts masked surface pixels into a median 3D surface point.
   A surface point is NOT an object center, grasp pose or contact annotation.
4. The visual assessor selects detection IDs for independent target objects,
   excluding group boxes and non-targets. It reads RGB plus overlays, detected-box zooms and the declared visual criteria.
   It may report true, false or unknown, with evidence and confidence. It does not
   know the hidden target polarity. Occluded terminals must remain unknown.

Detection failure never falls back to simulator poses. Motion invalidates the
inspection. A fresh inspection can find targets again. Spatial aliases left/right
refer to measured world-x ordering, not simulator object identity. No persistent
identity is guaranteed if objects cross. The model cannot establish electrical
success; only visible evidence can support an Agent completion claim.
