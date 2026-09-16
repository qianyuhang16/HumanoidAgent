# Mem-0 / RMBench 本机试跑报告

日期：2026-09-16。GPU：RTX 5090 32 GB，与另一个 GPU 作业共享。
固定种子：100000；任务：`cover_blocks / demo_clean`；机器人：双臂 Aloha。

## 结论

**已经接通真实 Agent → Mem-0 executor → SAPIEN，并完成一次六阶段整任务。**
但光追渲染存在间歇阻塞，普通渲染下模型表现又发生变化，当前不能可靠判断
IK 是否改善 VLA，也不能声称复现官方 76% 成功率。

本次不计算统计成功率。完整尝试记录见 [json/results.json](json/results.json)，
运行口径见 [json/metric_contract.json](json/metric_contract.json)。

## 实测结果

| 尝试 / 渲染 | 控制模式 | 环境阶段 | VLA 动作步 | 结果 |
|---|---|---:|---:|---|
| smoke-01 / 光追 | 全 VLA | **6/6** | 1128 | **完整成功，reward=1.0** |
| smoke-01 / 光追 | IK 接近 + VLA | 1/6 | 最后确认 95 | 第 99 步附近停止取得图像，中断 |
| smoke-03 / 光追，仅头部采集 | IK 接近 + VLA | 4/6 | 最后观察 701 | 第 703 步卡在相机获取图像，中断 |
| smoke-04 / 普通渲染 | 全 VLA | 1/6 | 571 | 第二个技能达到 400 步预算，停止 |
| smoke-04 / 普通渲染 | IK 接近 + VLA | 0/6 | 177 | 结束分类器触发，但物理效果失败 |

另有 smoke-02 的两次启动失败：路径规范化展开了虚拟环境解释器的符号链接，
导致 overlay 中的 `omegaconf` 不可见。已修复，两次均未调用 Agent 或执行动作，
原始记录保留。smoke-03 中计划的全 VLA 测试在启动前取消，没有虚构该次结果。

`VLA 动作步` 不含 IK 轨迹内部物理步。中断行不是完整轨迹计数，不能拿来比较
控制效率。IK 使用仿真真实物体位姿；两种模式的墙钟耗时也受共享 GPU 影响。

## 确实使用了我们的 Agent

成功的 smoke-01 由项目现有 `Agent + StructuredPlanner + Executive` 执行，
通过本机 Codex CLI 调用 `gpt-5.5 / medium` 共 6 次。没有实例化官方 Planner。
实际决策如下：

| 技能 | 绑定 | VLA 动作步 |
|---|---|---:|
| COVER | left | 149 |
| COVER | middle | 198 |
| COVER | right | 197 |
| UNCOVER | middle, red | 213 |
| UNCOVER | left, green | 178 |
| UNCOVER | right, blue | 193 |

固定 benchmark 目标约束了前三步的顺序，各只有一个合法候选。
红色揭盖时有三个位置候选，Agent 根据初始 RGB 选择中间；绿色揭盖时选择左侧。
初始图像作为显式视觉记忆保留，未来颜色位置映射没有提供给规划器。
Codex 在临时目录内运行，关闭 shell 工具，要求仅使用提供的观察与候选。

世界在整轮任务中仅初始化一次，没有在技能交接时重置，也没有执行完整专家
抓放程序。成功来自官方物理状态机，而非 Agent 自报或直接写入技能效果。

## 对中间层最有价值的失败记录

普通渲染 IK 组的首个技能在 177 步后收到两次结束信号：

1. 后端报告 `execution=completed`，控制权正常交还。
2. 环境仍为 `covered:left=false`，Executive 判定 `effect=failure`。
3. Agent 随后请求索引 0 重试；此时一次尝试预算已耗尽，合法候选为空。
4. StructuredPlanner 拒绝该索引，未下发新的动作，整个任务没有被标记成功。

这验证了“执行器结束”和“目标实际完成”必须分开处理，以及合法候选校验确实
拦截了模型的不合规请求。**当前没有自动修正抓取的恢复策略**，也没有验证
一种新的、更优的交接方法；该记录可以作为后续研究交接条件与恢复规则的案例。

## 渲染兼容性与证据边界

smoke-03 的 `SIGUSR1` 堆栈停在：

```text
envs/camera/camera.py:336  camera.get_picture("Color")
envs/_base_task.py:461    get_obs
simulation/rmbench_worker.py  chunk
```

上游有 [Blackwell / SAPIEN 3.0.0b1 同类阻塞报告](https://github.com/RoboTwin-Platform/RoboTwin/issues/477)。
本机已确认阻塞位置，尚未确认底层驱动根因。关闭未使用相机没有彻底解决问题；
其首个技能的 95 个动作及 95 张头部图像与第一轮逐项完全一致。

普通渲染的配对测试完成了运行流程，没有再次发生上述阻塞，但两种模式都未
完成整任务。它改变了图像外观，不能替代官方光追条件下的评测。
一次种子不足以区分视觉分布、控制器交接和策略随机性的影响。

模型使用发布的任务专用 checkpoint 和归一化统计，SHA256 校验通过、参数严格
加载。兼容改动包括 SDPA、FP32 动作头输入显式转换和可配置渲染。
这不是 π0.5 base 的零样本评测。

## 查看与复现

- 网页总入口：<http://127.0.0.1:8085/>
- 完整成功回放：<http://127.0.0.1:8085/smoke-01/>
- 普通渲染配对结果：<http://127.0.0.1:8085/smoke-04/>
- 原始日志、逐帧图像及视频：`workspace/mem0/smoke-*/`。
- 部署与运行命令：[README.md](README.md)。

代码回归测试：45 项，42 通过，3 项依赖其他环境而跳过。
新增测试覆盖了控制租约撤销后，迟到的模型预测不会继续发往模拟器。
