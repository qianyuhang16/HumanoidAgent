# 005 — 能力后端装配与精简 Tool 接口

新增 StateBackend/StateRequest/StateResult，按配置 state.backends 绑定后端与版本化契约。
运行时提供只读 state.query/read，以及存在后端时的 state.estimate；后台可直接使用同一
能力接口。估计器只返回判断，运行代码负责校验和采纳，不向后端开放状态修改对象。

回退是显式配置：默认仅在 unsupported 时尝试后续实现，error 需单独声明；保留每次尝试，
来源精确到后端和实现索引。不同来源产生冲突时保留双方。原始大输出通过 artifacts 引用。
主 Agent 读取后端结果后可再提交 claims，引用已有结果，形成外部模型＋Agent 的组合路径。

模型上下文包含精简状态与契约；完整证据按需读取。HTTP JSON 和 Codex 严格输出传输都支持
状态判断与 goal_id；Codex 的任意类型值通过 value_json 传输后恢复。

验证：覆盖配置装配、接口替换、显式回退、错误不自动回退、请求语义校验、跨来源冲突与
Codex 编解码一致性；默认核心现在提供 session.read/search 与 state.query/read 四个工具。
