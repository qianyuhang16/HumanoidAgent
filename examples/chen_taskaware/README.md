# Chen 场景留存示例（纯 synthetic，新平台接口）

独立示例分支内容：用框架现有 `TaskService → Agent → ModelPlanner → ToolRouter → Memory`
与真实本地 stdio 平台进程（`open_platform` / `serve`），演示同任务 scene revision 与
effect ledger、发布前证据门和重开完整性复核。**当前只有 synthetic 验证**，没有真实模型、
真实仿真、机器人或二号任务专家参与。核心、`application.py`、RoboCasa 均未修改。

## 入口与替换点

- **构建入口**：`examples.chen_taskaware.host.build(task=..., vlm=..., connection=...,
  run_dir=..., acknowledge_synthetic=True, resume=False)`，异步上下文管理器，产出 `App`
  （`run / reply / pause / query / report`）。`vlm` 必须由调用方提供，生产入口没有默认假模型。
- **平台进程**：`connection` 为框架标准 `{command, args, cwd, env}`，示例为
  `python -m runtime.server`，`cwd` 指向本目录。平台自己读 `platform.json`、
  `profiles/scene-revision-synthetic.json` 和 `skills/**/skill.md`，宿主不读平台文件。
- **真实替换点**：
  1. `vlm`：换成 `primitive_vla.vlm.config.VLMConfig` 连接的真实模型（本轮未运行）。
  2. 工具实现：每个工具一个 `skill.md`，`entrypoint: runtime.synthetic:factory`。
     接入真实执行/观察/验证时，新增 Profile 与对应 `skill.md` 指向真实 factory，
     不需改宿主或核心；synthetic Provider 均标 `synthetic.chen_taskaware`。
  3. 效果判定：`legacy/li_task_effect/` 为李佳瑾 v0.1.1 计算核心原样复制，仅接受
     `origin=synthetic` 的 case；真实证据需另行授权的真实观察 Provider。
- TaskAwareAgent、放碗/倒水专家 Profile 不在当前树中，**未对接**。

## 当前任务合同（受限）

`legacy/ledger.validate_task` 只接受固定字段：两个具名物体（Li 角色 甲/乙）放入同一容器，
加 protected/movable 名单、显式 `retain_on_publish`、证据时效（≤300 s）和公开约束列表。
这是旧 v4/v4_1 的**窄语义移植**：不迁移旧候选 unknown/几何历史等完整上下文，
**不声称支持任意 Scene200 任务或原完整上下文**。被移动物体只记 `moved_geometry_unknown`，
场景关系只发布经独立 Verification 成功的 `inside`。

## 两个独立的完整性边界

- **发布前（当前留存，`fresh=True`）**：`Ledger.publish_pending` 在构建下一个
  PlanningContext 前、且无活动/未终结调用时运行；对新 proof 及每个显式留存的旧 proof
  重读执行/观察/Verification 原生记录的摘要、**全部资源注册与 blob**（含只在观察记录中的
  空腔资源）、身份/版本/顺序/时效，重算 Li 判词，并确认原放置仍是该目标**最新已完成放置**。
  任何问题在追加 R2 前拒绝，写 `scene_publication_refused`（仅审计，链与效果不变、不续期）。
- **历史（重开与报告，`fresh=False`）**：`Ledger.verify_chain` 从 R0 用原生证据逐条重放
  全链，重读字节与结构，但不重新判时效、不续期。`Ledger.__init__` 重开时执行；
  `App.report` 每次执行，失败时摘要写 `task_effect_status=unconfirmed_integrity_check_failed`
  与错误，不会因资源损坏而写不出摘要。
- 已核实目标不重放的代码依据：`host.CallGuard` 在 `before_call` 拒绝对 `verified` 目标的任何
  调用、拒绝错版本/错 visit/错 attempt；发布门另行检查最新放置，不依赖 guard。
- failure/uncertain 不推进、不重复判：Verification 结论只有 `success` 能进入发布门；
  `Ledger.view` 对当前观察公开 `verification_call_id/verification_conclusion`，
  `CallGuard` 拒绝对已有终态 Verification 的同一观察再次调用 verify（同一证据重判不会改变
  Li 结论）。测试用脚本 VLM 见到非 success 结论即 `finish(outcome="failed")`。

## synthetic 容差与观察设定

`legacy/evidence.SYNTHETIC_TOLERANCE` 为 `boundary_band_m=0.02, inside_margin_m=0.005,
coverage_required=1.0`，取值与同级 v4 synthetic demo（`integrations/humanoid_continuation_v4/
demo.py` 的 `LI_BINDING["tolerance"]`，标注 Li fixture 01 工程容差）相同，**是作者设定的
synthetic 数据，不是实测机器人容差**。Li 规则为 `2σ ≤ boundary_band_m`，σ = 位置精度 + 空腔
精度。`synthetic_scenario`（`build(..., synthetic_scenario=...)`，平台持久化，重开不得变更）：

- `positive`：位置 0.004 + 空腔 0.005，2σ = 0.018 ≤ 0.02，几何余量 0.011 m ≥ 0.005 → success。
- `uncertain_precision`：位置 0.008 + 空腔 0.005，2σ = 0.026 > 0.02 → Li 正确给 uncertain。

此前版本误用 0.01 m 容差带，positive 夹具在 Li 下必然 uncertain（Linux 实测暴露）；
已改为自洽设定，Li 算法未改，uncertain 不被当作 success。

## 重开、未终结调用与 owner.lock

- 平台状态持久化在 `run_dir/native/platform-state/synthetic_world.json`；任务摘要变化或存在
  未结束的 synthetic 执行标记即拒绝启动。宿主 Memory 中有未结束调用时平台也拒绝初始化，
  不自动重放。
- `run_dir/owner.lock` 独占创建。只有 Agent、Router、平台关闭全部正常返回（停止已确认）
  才删除；业务异常但关闭成功同样删除。关闭失败（例如停止未确认）时保留，需人工排查，
  不自动解锁。异常本身不等同于停止未确认。

## 依赖与测试

Python ≥3.10、`jsonschema>=4.23,<5`、`PyYAML>=6,<7`（与仓库 `pyproject.toml` 一致）。
测试在仓库根目录运行，子进程保留父环境 `PYTHONPATH` 并追加 `src` 与仓库根：

```bash
PYTHONPATH=src python -m unittest examples.chen_taskaware.tests.test_integration -v
```

**Windows 已知限制（上游，未修补）**：框架 `primitive_vla/runtime/connections/server.py` 的
stdio `serve` 依赖 `loop.connect_read_pipe/connect_write_pipe`；在 Windows ProactorEventLoop
下遇到匿名 stdio 管道报 `OSError: [WinError 6]`，平台子进程在进入业务前退出。本示例不绕过、
不改核心。Windows 原生只能观察 4 项不启动平台子进程的 `ContractTests`；真实 stdio 集成测试
需在 Linux/WSL 运行，实际结果以外部 QA 报告为准。

测试使用真实 stdio 平台子进程；模型决策仅在 `tests/` 中脚本化，经真实 `ModelPlanner`。
测试通过只说明 synthetic 合同与证据链，不代表真实模型自主性、仿真或真机效果。
来源与原 SHA 见 `SOURCE_PROVENANCE.json`；Li 代码再分发许可在已读来源中未提供。
