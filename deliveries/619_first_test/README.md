# 619 单人首测交付

从 [TASK_SPEC](TASK_SPEC.md) 和 [SOP](SOP.md) 开始。用已有 Python 3.11 环境和 `requirements.txt` 的固定版本运行离线入口。缺依赖时记录模块及版本，交滕飞处理。现场回传用 [return_template.json](return_template.json)。

```console
python -B offline_619.py --out offline_result
```

输出目录应为新目录。`summary.json` 汇总结果，`chen-*` 下保存宿主、平台、原生调用与证据，`root/` 下保存本组账本、原观察封套和核验记录。`synthetic_task.json` 是可读的离线两物入盒输入。

|接点|本包如何演练|现场接入条件|
|---|---|---|
|共同框架|`versions.json` 固定 `f13616f` 的核心、`5b116b4` 的陈示例和组合树。`070ec4a` 是已核对的 main 祖先。本组文件以本地 `52bf6f3` 为基线，附上下文兼容补丁。|钱确认实际 checkout 与装配入口。框架默认应用装配普通 Agent，真实任务要明确使用何处的对象绑定、账本和完成门。|
|陈宿主|实际运行 `examples.chen_taskaware.host.build`、原 Skill 注册、Router、stdio 平台和证据链。离线入口提供 Windows 管道 I/O 适配、保留新版多轮上下文的适配和脚本 `decide`。陈自带宿主要求 synthetic acknowledgment。|先确认真实 Profile/Provider 和实际抓碗专家。现有陈任务合同限定两件物体入同一容器。|
|本组 TaskAwareAgent 与李的判别|用同一份陈合成任务做明确对象映射，再由本组 Agent、真实 LiEffectProvider 与 ChenT05Consumer 演练放置、独立核验、场景演进与完成。动作与测量为新构造测试输入。|原 Chen 工具用 `goal_id/visit/attempt/scene_revision`，本组执行门用 `item_id/receptacle_id/scene_version`；陈原观察使用 blob 且采用 Provider 结束时刻，本组现有合成合同与原生结束记录约束不同。两条链分别运行，实物接入要核同次 ID、时刻、资源和命题映射。|

合成脚本输出中的 `completed` 适用于本地工程记录。现场抓碗按具名技能的实际终点核对，入盒终态与真实 L4 证据另行成立。
