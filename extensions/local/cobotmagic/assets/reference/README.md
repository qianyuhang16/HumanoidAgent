# 官方 URDF 参考，不代表已匹配现场机器人

Downloaded 2026-09-23 from AgileX `piper_ros`, `noetic` branch:

- https://raw.githubusercontent.com/agilexrobotics/piper_ros/noetic/src/piper_description/urdf/piper_description.urdf
- https://raw.githubusercontent.com/agilexrobotics/piper_ros/noetic/src/piper_description/urdf/piper_description_old.urdf
- https://raw.githubusercontent.com/agilexrobotics/piper_ros/noetic/LICENSE

The three Piper files above are unmodified; their MIT license is retained in `LICENSE`.
Meshes are not included: these files are used for numerical kinematics only.
Firmware before S-V1.6-3 uses the old model; later firmware uses the newer model,
according to the upstream README. Confirm the installed arm/firmware, joint zero
conventions, and actual gripper TCP before choosing a model. The IK configuration selects the model; the TCP transform is loaded from the
selected arm’s accepted calibration result.

## 已核对的单臂 IK 配置

以下路径均相对于 `configs/ik.yaml`，两份文件均通过本地 XML 解析和六关节 FK/IK 测试。

| 现场固件 | `urdf` |
| --- | --- |
| >= S-V1.6-3 | `../assets/reference/piper_description.urdf` |
| < S-V1.6-3 | `../assets/reference/piper_description_old.urdf` |

共同配置：`base_link: base_link`、`tip_link: link6`，桥关节顺序为 `joint1` 到 `joint6`。`link6` 为末端 link，不等于夹爪指尖 TCP。新旧版本的 joint2 / joint3 零位模型不同；机器人侧实际使用的模型应优先于在线参考文件。

文件 SHA-256：

```text
c1a5c29ae9b91160557ed02bddd47365a71f0eb1653abb4997eb34f124021486  piper_description.urdf
7d10b29a675235007ed9bcbfccd61874ee9d2d3fefb7b96a2b9e5e28026735bb  piper_description_old.urdf
```

## COBOT MAGIC 整机参考

另已下载 `aloha_tracer2_dabai_dark.urdf`，来自官方 `mobile_aloha_sim` 的 `v2.0.0` 分支，固定到提交 `f799ae0192c0c2bbd502ec4d9bbec8c2633acd1a`：

https://github.com/agilexrobotics/mobile_aloha_sim/blob/f799ae0192c0c2bbd502ec4d9bbec8c2633acd1a/aloha_new_description/urdf/aloha_tracer2_dabai_dark.urdf

文件未修改，仅用于本地参考。为离线展示，已将它引用的 visual/collision meshes 从同一提交下载到 `mobile_aloha_sim/` 子目录，保留 package 内相对路径。上面的 Piper MIT 许可证不声明覆盖此独立仓库的文件。

Git 保留三份 URDF、此来源说明与 Piper 许可证。`mobile_aloha_sim/` 的下载网格约 205 MiB，由扩展 `.gitignore` 忽略，本机文件仍保留。重新克隆后如需运行截图脚本，从上述固定提交取得 URDF 所引用的网格，按 `package://` 后的相对路径放入该目录；普通数值 IK、标定和离线测试不需要这些网格。

离线解析发现整机有 52 个 link、51 个 joint，含底盘、相机及四条机械臂链。前方两臂的基座固定变换相对于 `body_Link` 为：

- `fl_base_link`: XYZ `[0.23875, 0.3, 0.775]` m，RPY `[0, 0, 0]`。
- `fr_base_link`: XYZ `[0.23875, -0.3, 0.775]` m，RPY `[0, 0, 0]`。

因此模型前方双臂间距为 0.60 m，与用户提供的约 60 cm 一致。这是型号几何的一致性线索，不是现场外参标定。

前右臂可解析链为 `fr_base_link` → `fr_link6`，关节为 `fr_joint1` 到 `fr_joint6`，已用当前 SerialIK 完成离线加载与 FK 核验。它的 link6 坐标轴和部分限位与单臂文件不同，不能将单臂的 TCP 变换直接套入。`fr_joint7/8` 的夹爪滑动关节原点也不自动等于抓取 TCP。

当前 `ik.yaml` 的 `robot` 部分 仍使用单臂链命名；没有仅凭整机产品名称自动更换链或选择固件。全部核验均离线完成，没有连接机器人。

## 离线模型截图

`../../scripts/render_model.py` 使用已有 tabletop worker 环境中的 SAPIEN 加载 visual 网格，并按 URDF 零位的固定变换构建静态模型。没有 ROS、网络、真实关节控制或物理运动；右侧执行臂人为着橙色，其余部分保留原网格外观。只展示机器人模型，不加载旧碗口/悬停估计，也没有建模真实桌面。

在项目根目录重新渲染：

```bash
extensions/local/tabletop/.venv-worker/bin/python extensions/local/cobotmagic/scripts/render_model.py
```

输出在 `workspace/cobotmagic-model/`：`overview.png`、`front.png` 和 `render.json`。`model.html` 可在浏览器中打开并切换视角；`model-screenshot.png` 是带图例的页面截图。模型使用官方整机链，不代表当前脚本采用的单臂 URDF 已与实机完成标定。
