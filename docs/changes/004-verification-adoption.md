# 004 — 验证目标、运行结果与采纳规则

验证可针对动作或声明的 goal_id；同轮主 Agent 判断继续可用。独立验证器支持旧判断返回值，
也支持 AssessmentResult，把 unsupported/error 与 satisfied/unsatisfied/unknown 分开记录。
新增可选 policy.goals、before_tools、completion。默认仍是建议模式，无额外阻塞或调用。
配置条件后，派发入口和完成入口检查证据新鲜度、执行关联、来源冲突、对象声明及历史状态。

独立验证工具 verification.assess 可按目标请求评估；目标要求发送给 Agent。
每次判断写入状态历史，晚返回的旧证据不能覆盖更新的结论。无采集时间的旧图片可继续作为
建议证据，但不能通过严格的新鲜度条件。代码检查证据关系，不宣称证明视觉判断正确。

验证：新增测试覆盖条件拦截、动作失效、冲突、过期、乱序、旧证据、未知目标与 unsupported；
原验证测试继续覆盖默认自主决策和主 Agent 同轮提交，不引入自动动作重放。
