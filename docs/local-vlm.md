# 本地 Qwen3-VL 4B

本机完整权重位于 `unsloth/Qwen3-VL-4B-Instruct` 的 Hugging Face 缓存；官方 `Qwen` 命名空间缓存只有处理器文件。实际加载路径记录在 [`configs/qwen-server.json`](../configs/qwen-server.json)。不需要重新下载模型。

## 启动模型服务

在项目根目录运行：

```bash
PYTHONPATH=src /home/estar/miniconda3/envs/affordance/bin/python \
  -m primitive_vla.providers.local_server --config configs/qwen-server.json
```

使用现有 `affordance` 环境中的 Transformers 4.57.1、PyTorch 2.7.1+cu128，在 RTX 5090 上加载 BF16 权重。模型服务与 Agent 进程独立，Agent 本身无需安装 PyTorch。

新环境可安装 `.[local-vlm]`，并在服务配置中设置实际权重目录。加载方式基于 [Qwen 官方模型说明](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct) 的 Transformers 接口，底层调用见 `providers/local_server.py`。

`qwen-server.json` 配置模型路径、服务模型名、监听地址、GPU、精度、注意力实现、图像像素上限和输出 token 上限。当前服务提供：

- `GET /health`：模型就绪状态。
- `GET /v1/models`：可用模型 ID。
- `POST /v1/chat/completions`：文本或多图像输入，非流式返回。

本地服务按请求串行推理。图像支持 data URL 或 HTTP(S) URL；服务通过提示词要求 JSON，不提供约束解码，所以 Agent 端仍解析并验证模型输出。

## 接入网页与仿真

```bash
# 符号厨房与本地模型
.venv/bin/python -m primitive_vla serve \
  --config configs/qwen.json --workspace workspace/qwen

# SAPIEN 相机与本地模型
.venv/bin/python -m primitive_vla serve \
  --config configs/sapien.json --workspace workspace/sapien
```

分别打开 **http://127.0.0.1:8082** 和 **http://127.0.0.1:8081**。本地模型无需 API 密钥。网页可配置解析器、规划器、验证模式、模型地址、提示词和超时。

当前本地示例采用：

```text
自然语言 + 当前图像 + SKILL.md
              ↓ Qwen3-VL
           结构化目标
              ↓ 规则规划
           合法技能调用
              ↓ 确定性执行
         观测状态与效果验收
```

Qwen 负责理解目标；规则规划根据实时状态组合从 Markdown 加载的技能。可在网页将 `planner.type` 改为 `vlm`，让模型逐步选择合法候选；验证也可独立改成 `audit` 或 `enforce`。当前默认组合来自实测选择，不是模型失败后的自动回退。

## 验证范围

真实本地模型已完成：

- 描述 SAPIEN 相机图像中的机械臂、容器、搅拌棒与三种颜色物体；响应存于 `workspace/qwen-check/vision-validation.json`。
- 在 SAPIEN 网页中提交“三种颜色配料入碗、搅拌、工具归位”的非模板指令，Qwen 接收相机图像并解析目标，文件化技能完成九步物理执行。[查看任务](http://127.0.0.1:8081/?task=700d24eea2de41a18a6555815cde5a04)，结果保存在 `workspace/qwen-check/sapien-validation.json`。
- 将非模板指令“请把红色水果放进盒子，最后把盒盖关好。”解析为入盒与关盖目标；规则规划执行五步，最终观测确认成功。

4B 模型直接逐步规划的试运行出现过反复抓放与重复请求观察；初始目标输出也出现过依赖格式错误。解析提示已补充目标语义和依赖类型，非法结构仍会被拒绝。上述单次识别与任务通过不代表任意场景的规划成功率；实际任务记录保留了失败与成功运行。
