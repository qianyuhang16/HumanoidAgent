# Git 与开发约定

## 当前基线

本地仓库的 `main` 从 2026-09-16 撤下 research 001–004 后的 Agent＋VLA 主线开始。标签 `baseline-agent-vla-20260916` 标记首个提交。更早的修改没有 Git 历史，不补造历史提交。

旧研究源码、日志、视频及回退清单保留在 `workspace/archive/research-20260916/`，不纳入 Git。该目录中的 `rollback.json` 记录恢复和移出的文件；`source-before-rollback.tar.gz` 保存撤下前的源码。运行数据仍需单独备份，Git 不替代数据备份。

仓库目前仅在本机管理，尚未配置远程仓库。

## 日常开发

每个研究方向或功能使用独立分支，把可运行的阶段作为提交点：

```bash
git switch -c research/agent-primitive-middleware
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

建仓前恢复主线后的结果：33 项测试，30 项通过，3 项需要物理仿真环境的测试默认跳过。这不代表重新运行了真实 VLM/VLA 或 GPU 仿真实验。
