# 离线演示扩展

本包包含软件 MockRobot 与电池试错演示。它们通过公开工具接口接入，核心不默认注册这些工具；演示不代表真实物理效果或 VLM 推理能力。

在仓库根目录安装并运行：

```bash
python -m pip install -e ./extensions/demo
primitive-vla run --config extensions/demo/configs/battery.yaml --workspace workspace/battery-demo \
  --instruction "装入电池，调整方向直到电源表转动"
```

`configs/mock.yaml` 可连接实际 LLM/VLM 操作软件机器人，需要填写模型地址和 ID。

不安装扩展也可通过显式 Python 路径运行离线测试：

```bash
PYTHONPATH=extensions/demo/src .venv/bin/python -m unittest discover -s extensions/demo/tests -v
```
