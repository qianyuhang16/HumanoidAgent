# 031：状态估计归入 state

删除顶层 capabilities 包：StateRequest、StateResult、StateBackend 移到 state.backends，
StateCapabilities 更名为 state.estimation.StateEstimator。Runtime 使用 estimator 装配
后端；估计、证据保存与工具暴露仍分文件维护，估计后端不获得修改 journal 的入口。

同步核心、测试和当前文档的导入。不保留空 capabilities 转发层；外部 Python adapter
需将原 capabilities.contracts 导入迁移到 state.backends。配置 state.backends、
state.schemas 和 state.estimate 的输入输出保持兼容，包括旧 capability_status 字段。

验证：运行核心回归，覆盖配置式后端、回退、证据校验、导航、多模态及异步执行；
检查生产与测试中不再引用旧包。未修改具体模型或机器人控制协议。
