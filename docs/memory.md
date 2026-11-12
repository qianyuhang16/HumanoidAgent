# 文件记忆、遗忘与定时整理

这次设计参考 RPent 固定提交 `b96cbc15d9ba73cf47dc63e3c2512d423bae5365` 的
[MemoryManager](https://github.com/RLinf/RPent/blob/b96cbc15d9ba73cf47dc63e3c2512d423bae5365/rpent/memory/manager.py)
与[目录和发布说明](https://github.com/RLinf/RPent/blob/b96cbc15d9ba73cf47dc63e3c2512d423bae5365/docs/source-zh/rst_source/development/memory.rst)。
借鉴的是可读经验文件、MEMORY.md 索引、来源保留和合并发布；RPent 的草稿合并不等于本项目新增的定时 VLM 整理。
这里继续使用通用长短期生命周期，不固化机器人任务族，也不引入 RPent 的固定动作重放流程。

## 存储与可读性

```text
workspace/
  MEMORY.md                 长期记忆索引
  short/<record-id>.md       当前工作记忆
  long/<record-id>.md        跨任务保留的知识、计划与历史信息
  archive/<id>-<version>.md  整理引用的原文版本及被合并的旧笔记
  journal/<record-id>.json   原始事件日志，只追加，不随工作记忆删除
  sessions/<session-id>.json 会话与预算状态
  resources/refs/<id>.json   多模态资源元数据
  resources/<sha256>         原始图像、地图等字节
  organization.json         已处理版本的整理进度
```

运行路径没有数据库。Markdown 的 frontmatter 使用 JSON（YAML 的子集），无需新增 YAML 解析依赖。
Planning 笔记正文是自由 Markdown；Tool 业务 payload 保留为可读的 JSON 代码块，公共元数据在 frontmatter。
正文是工作记忆的来源，检索/读取会读取文件；不是把数据库内容临时导出成展示文件。
文件以临时文件替换完成单文件更新。系统仍是单进程、单事件循环，不提供跨进程事务或并发写入仲裁。

## VLM 的记忆操作

| 操作 | 作用 |
| --- | --- |
| `memory_index` | 分页读取长期索引 |
| `memory_search` | 条件、字面关键词检索与分页，不宣称语义检索 |
| `memory_get` | 分页读取某份 Markdown |
| `memory_write` | 写入 title、content、source_ids、tags 和生命周期 |
| `memory_update` | 修改当前 Session 的 Planning 笔记，不改变来源或 Tool 证据 |
| `memory_delete` | 删除当前 Session 的短期工作文档 |
| `evidence_get` | 读取原始事件；指定 version 时读取整理实际采用的来源版本 |
| `get_resource` | 读取资源引用，由通用 ModelPlanner 按需取得图像字节 |

删除短期文档后，文件消失，后续检索和默认上下文不再包含它。决策日志和动作结果回显只保存在 journal，
不会通过这些副本把已删除笔记重新加入上下文。journal 可通过显式证据读取访问；这属于工作记忆遗忘，
不是隐私数据的彻底擦除。

桌面时间线通过独立的历史/证据接口读取 journal，包括详情、复制和翻页；Memory 页面只查询当前工作文档。
因此已遗忘的观察仍可用于事后审计，但不会自动送回 VLM 上下文。

原始日志、资源及 Session 独立保留，所以删除观察或动作记录不能释放控制资源、改变动作状态，
也不能让重启恢复遗漏未确认停止的调用。长期文档不能通过短期删除接口清除。

## 定时长期整理

`MemoryOrganizer(memory, vlm, interval=300)` 与 ModelPlanner 使用同一个 VLM 接口。
它有自己的职责提示词，但不存在某个模型厂商专用的整理提示词。

每个周期先清理过期短期投影并更新索引，再在文档数和字符预算内读取新增/变更的长期文档、Planning 笔记、
验证与失败记录。不会逐帧总结所有观测。默认最多处理 16 个新输入文档，并附少量既有整理笔记作为合并参考。
已处理的内容指纹持久化；内容没有变化时不调用模型。过大的单份文档返回 `context_budget`，不悄悄截断后发布。

模型提出合并摘要，发布入口统一验证：

- 引用只能来自这次实际提供的文档；替换对象必须是被引用的长期 Planning 笔记。
- 原始 Tool/Verification 文档不能被模型摘要替换。
- 模型请求期间若来源被修改、过期或删除，提案返回 `stale`，下一周期重新读取。
- 引用文档先保存精确内容版本，新笔记保存 `source_ids` 与 `source_versions`。旧笔记随后退出工作索引。
- 新笔记标记为 `planning / memory.organizer / synthesized / model_summary`。这些标签不是任务成功或事实已验证的证明。

不能用模型摘要替代 Verification。版本标识是规范化 Markdown 内容的 SHA-256；即使源笔记之后再次编辑，
仍可通过 `evidence_get(id, version)` 取回整理实际读到的版本。

```python
organizer = MemoryOrganizer(memory, vlm, interval=300)
app = create_app(agent, robots=robot_profiles, organizer=organizer)
```

桌面应用 lifespan 启动周期任务，退出先取消整理请求，再停止 Agent/Provider。未配置整理器时，
默认只做每 60 秒的 TTL 清理与索引更新，不调用模型。嵌入其他宿主时，可自行调度 `once()` 或 `run(stop_event)`。
后台错误会在 `/api/status` 的 `memory_maintenance` 中显示，并停止该整理任务；不会静默无限重试。
重建宿主后可从持久化进度继续，原始数据不因整理失败被删除。

## 旧工作区迁移

旧 SQLite 目录不会被新版本当作空目录打开。请在停止旧写入者后，显式导入一个不存在的新目录：

```bash
.venv/bin/python -m primitive_vla.memory.migrate workspace/agent workspace/agent-files
.venv/bin/primitive-vla desktop --workspace workspace/agent-files
```

迁移器只读旧数据库，在同一个读取快照中导出记录、资源和 Session，保留 ID、时间、关联、预算与未结束调用，
校验本地资源的 SHA-256。原目录保持原样，不删除旧数据库；目标已有内容时拒绝覆盖。
迁移报告保存为新目录的 `migration.json`。迁移工具是唯一读取 SQLite 的可选路径。

仿真或机器人状态的恢复仍由对应 Provider 负责；把 Memory 迁移成功不能当成物理场景恢复成功。
正在运行的旧进程不会热切换存储，迁移快照之后的新写入也不会自动同步。

## 验证

测试覆盖 Markdown 重开与正文更新、短期删除和上下文遗忘、原始证据/控制恢复保留、迁移一致性、
来源伪造拒绝、长期合并、版本溯源、并发修改冲突、周期触发、请求取消、错误可见及预算限制。
这些是存储和接口契约验证；不构成长期知识语义正确率或真实机器人任务成功率的证明。
