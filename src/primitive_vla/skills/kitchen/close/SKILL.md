---
name: CLOSE
version: '1'
description: 已声明盒型关盖；结束时双手空闲。
parameters:
  target:
  - box
preconditions:
  healthy: 'true'
  free:right: 'true'
  free:left: 'true'
  reachable:{target}: 'true'
  open:{target}: 'true'
invariants:
  healthy: 'true'
effects:
  open:{target}: 'false'
  accepts:{target}: 'false'
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
---

# CLOSE

仅在盒盖已确认打开且所需双手均空闲时使用。需要先装入物体的任务，应遵守任务依赖，在入盒完成后关盖。
