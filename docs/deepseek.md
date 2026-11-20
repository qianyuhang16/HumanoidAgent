# VLM 接口与 DeepSeek / GPT

模型通信位于 `vlm/`。`planning/ModelPlanner` 负责共享提示词、上下文、框架函数定义和按需图像读取。
更换模型只需注入另一份 `VLM` 实现，不复制 Planning 提示词，也不修改 Agent 的事件循环。

```text
ContextBuilder → ModelPlanner → VLM.generate(request) → VLMResponse → Decision → Runtime
                                ↑
                       MemoryOrganizer 也使用此接口
```

## 模型接口

`VLMRequest` 包含 instructions、functions 和用于 Agent 的有序 messages。单轮文本与附件输入使用 text、images。
图像输入包含字节和媒体类型。`VLMResponse` 返回文本及解析后的函数调用。
模型适配器不导入 Planning、Memory、Runtime 或机器人 Provider，不决定业务动作顺序。

`planning/prompts.py` 是统一规划提示词；Memory 整理有独立的职责提示词，两者均与模型厂商无关。
`ModelPlanner` 暴露一个 `decide` 函数，其参数包含 `action` 和 `arguments`，要求一轮一个调用。
Runtime 校验动作契约，Router 校验具体 Tool 参数。模型的错误函数名或多调用响应会被拒绝，
下一轮获得收到的调用和正确封装格式，仍受既有推理与连续错误预算约束。请求设置不能替代返回值检查。

## DeepSeek 使用

```bash
.venv/bin/python -m pip install -e '.[gui,vlm,test]'
```

```python
import os
from primitive_vla.planning import ModelPlanner
from primitive_vla.vlm.deepseek import DeepSeekVLM
from primitive_vla.memory.organizer import MemoryOrganizer
from primitive_vla.interfaces.web import create_app

vlm = DeepSeekVLM(os.environ["DEEPSEEK_API_KEY"], model="deepseek-flash")
planner = ModelPlanner(vlm, read_resource=memory.read_resource)
# 将 planner 注入已有 Agent，并由宿主装配机器人与工具。
# agent = Agent(planner, registry, memory, skills, resources)
organizer = MemoryOrganizer(memory, vlm, interval=300)
app = create_app(agent, robots=robot_profiles, organizer=organizer)
```

`memory`、`registry`、`skills`、`resources` 和 `robot_profiles` 来自应用自身装配，框架不提供假机器人。
工厂通过既有 `primitive-vla desktop --factory module:function` 接入桌面。
未配置模型的默认桌面入口仍提供草稿和历史查看。

Agent 多轮对话使用 DeepSeek 的 [Responses API](https://api-docs.deepseek.com/guides/responses_api/)。
助手发出的 function_call 和对应 function_call_output 使用同一协议 ID，图片作为结果内的 input_image 内容发送。
单轮 Memory 整理等调用使用 [Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)。
默认超时 60 秒、输出上限 2048 tokens。Planning 关闭思考模式以使用强制函数调用。HTTP 支持取消，错误由调用方处理。
自定义 DeepSeek base_url 用于 Agent 时需要提供 /responses。

各轮只加载有界历史和 Session 保留的图片，默认最多 2 张，由 Limits.context_images 配置。
图片与相机名、尺寸和短引用一起挂到对应结果上。图像编解码与限制见 [DeepSeek Vision](https://api-docs.deepseek.com/guides/vision/)。
该接口依据公开 API 实现，DeepSeek 网页内部的提示和裁剪策略没有用于实现。

密钥通过环境变量或被 Git 忽略、权限为 600 的本地配置传给构造器；不进入源码、日志、Memory 或提交。
对外发送的是调用方组装的任务上下文、工具返回的图像及明确读取的历史图像证据。

## 验证范围

`tests/test_vlm.py` 覆盖共享 Planning 提示词、通用文本调用、函数 Schema、图像字节、
错误反馈和暂停取消；`tests/test_module_boundaries.py` 检查模型实现不依赖 Agent 业务模块。
文件记忆及定时整理见 [Memory 设计](memory.md)。模型接入检查不是自主机器人任务的成功率评测。

## GPT 与独立配置

模型配置单独保存到 `configs/vlm.json` 或指定文件，由 `VLMConfig.load(path).connect()` 加载。
文件只含 provider、model、api_key/api_key_env 和模型请求设置，不含平台、profile 或 Skill。
通用示例为 `configs/vlm.deepseek.example.json`、`configs/vlm.gpt.example.json`；
同一份模型配置可与任意平台启动配置组合，VLM 模块不读取平台文件。

`vlm.gpt.GPTVLM` 使用 [OpenAI Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create)，
将文本、图像字节及调用方函数定义转为请求，解析 function_call/output_text；不带 Planning 提示词或机器人逻辑。
图像使用 Base64 data URL，参见 [官方图像输入文档](https://developers.openai.com/api/docs/guides/images-vision)。
函数请求设置 `tool_choice=required`、`parallel_tool_calls=false`、`strict=false`，保持本框架参数 Schema 的可选字段语义，
参数仍由 Runtime 校验；格式依据 [官方函数调用文档](https://developers.openai.com/api/docs/guides/function-calling)。

```python
from primitive_vla.vlm.config import VLMConfig

vlm = VLMConfig(provider="gpt", model="gpt-6-astra", api_key_env="OPENAI_API_KEY").connect()
# 或 VLMConfig(provider="deepseek", model="deepseek-flash").connect()
planner = ModelPlanner(vlm, read_resource=memory.read_resource)
```

模型名应选择账号实际可用的型号。GPT 默认 API 地址为 `https://api.openai.com/v1`，
使用 `max_output_tokens` 控制输出预算，预算也可能被推理消耗。请求设置 `store=false`，各轮由框架组装上下文，
不在适配器内部创建服务端会话、重试或执行工具。HTTP 错误、截断输出、拒绝和无效函数 JSON 向调用方传播。

桌面入口已改为独立模型配置 + 两层能力装配，见 [装配说明](assembly.md)。本轮 GPT 验证使用 HTTP 替身，
覆盖图像、函数、纯文本、错误与取消；没有据此宣称已完成真实 GPT API 或自主机器人任务验收。
