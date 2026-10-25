# 017：main 生产路径与安装包检查

2026-10-25。用户要求提交重构并确认 main 没有生产 mock 层。
之前的五个多模态重构提交已在 main，最新为 b21fcf1；此次补充检查记录。

## 检查结果

- 检查核心 src/primitive_vla、Cobotmagic 的 src 及已跟踪配置，未发现 mock/fake
  后端、模拟运行开关或失败时返回伪造数据的替代实现。UI 中的 placeholder 是空状态提示。
- 对两个生产源码目录内 97 个已跟踪 Python 模块执行 AST import 检查，未发现
  对 tests、unittest、pytest 或 mock 的导入。
- 核对模型 adapter 装配、真实相机读取、感知模型加载与 VLA 后端入口。
  默认模型连接使用 HTTP/Codex；无效 adapter 报错。相机从桥读取帧，感知加载本地
  模型权重，VLA 使用配置的策略与机器人反馈，没有自动切换到假实现。
- 从 Git 已跟踪文件复制到临时目录，使用现有 setuptools 构建两个 wheel，未联网：
  primitive_vla-0.3.0-py3-none-any.whl 共 63 个文件；
  primitive_vla_cobotmagic-0.1.0-py3-none-any.whl 共 56 个文件。
  逐项检查均仅包含对应生产包和 dist-info 元数据，无 tests、fixtures 或 research。

## 生产与测试范围

main 保留生产源码、配置模板、文档和回归测试。测试目录中的 fake/fixture 仅用于离线
验证，不作为生产实现发布，也不从生产源码导入。依赖注入接口仍用于真实 transport、
模型和机器人 adapter 的装配，不因测试会使用它们而删除。

没有发现需要删除的生产 mock 层，因此此次不修改运行行为，不删除回归测试，也不创建
新的替代后端。未跟踪的 research/ 未纳入提交。构建检查未启动真实设备或加载模型权重。
此前的功能回归结果见 [016](016-multimodal-documentation.md)；本次仅补充审计文档。
