---
name: OPEN
version: '1'
description: 已声明盒型开盖；结束时双手空闲。
parameters:
  target:
  - box
preconditions:
  healthy: 'true'
  free:right: 'true'
  free:left: 'true'
  reachable:{target}: 'true'
  open:{target}: 'false'
invariants:
  healthy: 'true'
effects:
  open:{target}: 'true'
  accepts:{target}: 'true'
  free:right: 'true'
  free:left: 'true'
resources:
- right_arm
- right_hand
- left_arm
- left_hand
- zone:{target}
capability:
  mode: symbolic_mock_only
  lid: hinged demo box
execution:
  type: program
  steps: []
---

# OPEN

仅在盒盖已确认关闭且所需双手均空闲时使用。开盖后必须由观测确认 open 与 accepts；结束时保留真实持物状态。

## 技能衔接

向关闭的盒子装物时，先腾出双手并打开盒盖，再抓取物体。即便最终目标要求盒盖关闭，中间仍可先开盖；应在装入物体后再关闭。
