---
name: PLACE
version: '1'
description: 完整转运、放置和释放；调用结束不额外执行转运。
parameters:
  object:
  - cup
  - spoon
  - fruit
  target:
  - support
  - rack
  - box
  hand:
  - right
preconditions:
  healthy: 'true'
  holding:{hand}:{object}: 'true'
  accepts:{target}: 'true'
  reachable:{target}: 'true'
invariants:
  healthy: 'true'
effects:
  holding:{hand}:{object}: 'false'
  free:{hand}: 'true'
  at:{object}:{target}: 'true'
  ready:{object}: 'false'
resources:
- '{hand}_arm'
- '{hand}_hand'
- object:{object}
- zone:{target}
capability:
  mode: symbolic_mock_only
  placement: stable support or open box
execution:
  type: program
  steps: []
---

# PLACE

将持有物体放到指定支撑面或容器中。执行包含转运、释放和退让。目标是碗时用于投放配料；目标是工具区时用于归还工具。完成后读取实际位姿和持物事实确认，不补发第二次放置。

## 技能衔接

若为了开盖而临时放下物体，下一步应打开盒子，避免在开盖前再次抓取形成循环。优先放回已知的原支撑位置。
