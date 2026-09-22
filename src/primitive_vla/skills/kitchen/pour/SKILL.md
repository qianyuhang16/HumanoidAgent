---
name: POUR
mock_faults:
- uncertain_pour
version: '1'
description: 右手将示例杯中的干颗粒倒完并恢复直立，继续持杯；不支持定量倾倒。
parameters:
  source:
  - cup
  target:
  - bowl
  hand:
  - right
preconditions:
  healthy: 'true'
  holding:{hand}:{source}: 'true'
  ready:{source}: 'true'
  nonempty:{source}: 'true'
  upright:{source}: 'true'
  reachable:{target}: 'true'
  event:poured:{source}:{target}: 'false'
invariants:
  healthy: 'true'
  holding:{hand}:{source}: 'true'
effects:
  event:poured:{source}:{target}: 'true'
  ingredients:{target}: 'true'
  nonempty:{source}: 'false'
  upright:{source}: 'true'
  holding:{hand}:{source}: 'true'
resources:
- '{hand}_arm'
- '{hand}_hand'
- object:{source}
- zone:{target}
max_attempts: 1
irreversible: true
recovery: 先观察实际材料变化；本轮任务不自动重试倾倒。
capability:
  mode: symbolic_mock_only
  pour: empty demo dry-granule cup; no precise dosage
execution:
  type: program
  steps: []
---

# POUR

把持有杯中的示例干颗粒倒入碗，再恢复直立并继续持杯。倾倒是不可逆操作；结果未知时先重新观察实际材料变化，本任务不自动重复倾倒。不宣称支持定量倾倒。
