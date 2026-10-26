# 018：工作区、旧文档与研究分支清理

2026-10-26。按用户要求清理不必要的文档、缓存及 research 分支。

- 删除已被当前架构／工具／配置说明覆盖的 backends.md、local-vlm.md、reference.md、
  simulation.md、skills.md，以及包含已删除模块和旧接口草图的 extensibility-proposal.md。
  原文仍在 Git 历史中；更新 development.md，去掉过时分支数量和旧测试统计。
- 清除 71 个 Python／下载元数据缓存目录、根目录 build/、旧 LeRobot build/，
  以及清理后为空的 scripts/、examples/ 和旧模块目录。合计约 3.5 MB 可再生成文件。
- 将未跟踪的小组讨论资料从 research/embodied-agent-discussion-2026-10-22/
  移到已忽略的 workspace/research/embodied-agent-discussion-2026-10-22/。
  归档后核对 168 个文件的相对路径及大小，与移动前一致。
- 使用 git branch -d 删除本地 feat/research。删除前确认 645f050 已是 main 的祖先，
  不存在仅在该分支上的提交；其他功能分支与远端分支未改动。
- 保留逐提交说明、有效接入文档、测试、虚拟环境及安装元数据；保留模型权重、
  标定数据、运行证据和 Git 备份，不将这些内容当作缓存删除。

验证：剩余已跟踪 Markdown 的本地链接全部有效；使用 python -B 检查生产模块导入与
默认模型配置，未重新生成字节码。未修改运行逻辑，因此不重复完整功能测试。
