# Git 与开发约定

## 当前基线

本地保留两个分支：`main` 已合入 Cobotmagic，保存通用 Agent 核心及机器人扩展；`feat/cobotmagic-extension` 继续保留，可用于后续扩展开发。标签 `baseline-agent-vla-20260916` 标记最早的 Agent＋VLA 基线提交。旧分支整理前的 Git 历史备份位于本机 `workspace/git-backups/`，不纳入 Git。

旧研究源码、日志、视频及回退清单保留在 `workspace/archive/research-20260916/`，不纳入 Git。该目录中的 `rollback.json` 记录恢复和移出的文件；`source-before-rollback.tar.gz` 保存撤下前的源码。运行数据仍需单独备份，Git 不替代数据备份。

本次分支整理只修改本地仓库，不推送或删除远程分支。

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

- 跟踪源码、`SKILL.md`、配置模板、文档、测试、示例和 benchmark 程序。
- 忽略虚拟环境、缓存、模型权重、凭据、日志和 `workspace/` 内所有运行数据。
- 配置文件中的本机路径可以按部署调整；密钥通过环境变量提供。
- 纯文档或 Git 配置修改检查差异即可；执行逻辑修改运行相关测试。

现有快速测试入口：

```bash
.venv/bin/python -m unittest discover -s tests -t . -v
```

打包前清理生成的 `build/` 缓存，避免源码移动或删除后旧副本仍进入 wheel。核心包不应包含机器人、仿真器、场景技能或具体模型加载器；扩展单独打包、单独运行其测试。

建仓前恢复主线后的结果：33 项测试，30 项通过，3 项需要物理仿真环境的测试默认跳过。这不代表重新运行了真实 VLM/VLA 或 GPU 仿真实验。
