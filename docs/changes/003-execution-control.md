# 003 — 独立执行控制模块

把现有校验、去重、资源占用、期限和停止处理迁移到 execution/ExecutionManager。
旧 ToolRuntime 导入保留为别名。运行时显式装配执行控制与状态记录，动作真正进入派发入口时
使现场视图失效，执行事件独立于模型工作摘要记录。

新增 ExecutionUpdate，分开表达物理阶段与控制权状态。显式 unknown 优先于旧
control_released 默认值。ExecutionTool 将 start/inspect/stop 后端适配成现有 Tool，
取消被接收不等于停止确认。旧 call/stop 扩展无需立即迁移，旧停止回执不推断目标达成。

验证：保留原有执行契约测试，增加异步执行超时、取消未确认、显式未知状态、动作失效与
只读并行测试。核心依赖隔离测试允许新的通用 execution 模块，仍禁止加载具体机器人依赖。
跨进程仲裁和远程 fencing 仍由机器人后端实施；本次没有接管底层实时控制。
