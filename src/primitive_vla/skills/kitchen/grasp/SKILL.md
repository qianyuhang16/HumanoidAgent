---
name: GRASP
mock_faults:
- timeout
- missed_grasp
version: '1'
description: 右手完整抓取；从支撑面到已确认持物姿态。
parameters:
  object:
  - cup
  - spoon
  - fruit
  source:
  - support
  - rack
  - box
  hand:
  - right
preconditions:
  healthy: 'true'
  free:{hand}: 'true'
  at:{object}:{source}: 'true'
  accepts:{source}: 'true'
  reachable:{object}: 'true'
invariants:
  healthy: 'true'
effects:
  holding:{hand}:{object}: 'true'
  free:{hand}: 'false'
  at:{object}:{source}: 'false'
  ready:{object}: 'true'
resources:
- '{hand}_arm'
- '{hand}_hand'
- object:{object}
capability:
  mode: symbolic_mock_only
  grasp: right hand, declared object categories
execution:
  type: program
  steps: []
---

# GRASP

在手空闲、目标可达且来源位置已确认时使用。完成后继续持物。先接近和闭合，再通过双侧接触确认；抬升后再次确认。失败时读取新观测再决定恢复，不能把动作返回完成当作抓取成功。

## 技能衔接

抓取之前向后看一步：如果目标是关闭的容器，且开盖需要双手空闲，先选择 OPEN，再抓取待放入的物体。不要刚放下物体去腾手开盖，又在开盖前把它抓起。
