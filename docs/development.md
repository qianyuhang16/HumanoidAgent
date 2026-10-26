# Git 与开发约定

## 当前基线

`main` 保存通用 Agent 核心及 Cobotmagic 扩展；`feat/cobotmagic-extension` 可用于后续扩展开发。已合入 main 的本地 `feat/research` 分支已清理；其他功能分支按各自任务维护。标签 `baseline-agent-vla-20260916` 标记最早的 Agent＋VLA 基线提交。旧分支整理前的 Git 历史备份位于本机 `workspace/git-backups/`，不纳入 Git。

旧研究源码、日志、视频及回退清单保留在 `workspace/archive/research-20260916/`，不纳入 Git。该目录中的 `rollback.json` 记录恢复和移出的文件；`source-before-rollback.tar.gz` 保存撤下前的源码。运行数据仍需单独备份，Git 不替代数据备份。

小组架构讨论原稿归档于本机 `workspace/research/embodied-agent-discussion-2026-10-22/`。
当前设计以 [架构](architecture.md)、[工具接口](tools.md)和[多模态契约](multimodal-contracts.md)为准，逐次变更见 [提交说明](changes/README.md)。过时文档仍可从 Git 历史检索。

## 日常开发

`main` 是集成主线；可在 `feat/cobotmagic-extension` 开发机器人扩展，并在验证后合并回 `main`。开始扩展开发前先合入 `main` 的最新改动。把可运行的阶段作为提交点：

```bash
git switch main
# 机器人开发时改用 git switch feat/cobotmagic-extension
# 修改后查看差异，按路径暂存本次工作
git diff
git add <本次修改的文件或目录>
git diff --cached
git commit -m "feat: describe the completed change"
```

查看基线与历史：

```bash
git log --oneline --decorate --all
git diff baseline-agent-vla-20260916
git status --short
```

已有提交需要撤销时，优先用 `git revert <commit>` 生成反向提交、保留历史。不要用整目录覆盖或 `git reset --hard` 代替日常回退。

## 文件范围与验证

- 跟踪生产源码、`SKILL.md`、配置模板、当前文档和回归测试。
- 每次提交只包含一个主题，并在 `docs/changes/` 保留说明。
- 忽略虚拟环境、缓存、模型权重、凭据、日志和 `workspace/` 内所有运行数据。
- 配置文件中的本机路径可以按部署调整；密钥通过环境变量提供。
- 纯文档或 Git 配置修改检查差异即可；执行逻辑修改运行相关测试。

现有快速测试入口：

```bash
.venv/bin/python -m unittest discover -s tests -t . -v
```

打包前清理生成的 `build/` 缓存，避免源码移动或删除后旧副本仍进入 wheel。核心包不应包含机器人、仿真器、场景技能或具体模型加载器；扩展单独打包、单独运行其测试。

缓存清理限于可再生成的构建文件与测试／Python 缓存；虚拟环境、可编辑安装元数据、模型权重、标定记录和运行证据不按缓存删除。具体回归结果和验证范围随提交记录，不将离线测试结果当作真实设备或模型效果。
