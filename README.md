# VelaLoom

<p align="center">
  <img src="assets/velaloom-logo.png" alt="VelaLoom logo" width="420">
</p>

一个机器人数据流水线工具集，将原始多模态数据编织、转换为结构化、标准化的数据集。

## AI 开发规范

面向 AI 开发助手的项目上下文、任务流程、架构设计和进展记录位于 [`.ai/`](.ai/README.md)。
AI 开始接收开发任务前应先阅读该目录入口文档。

## Foxglove 中查看 Kuavo URDF

仓库中的 `urdf/biped_s300053.urdf` 是从原始 `kuavo_assets` 包导出的模型，网格路径仍然指向
`package://kuavo_assets/...`。当前仓库没有这个 ROS 包，因此直接在 Foxglove 导入该文件时，
URDF XML 可以打开，但 STL 网格无法解析，最终模型不会正常显示。

请在 Foxglove Desktop 的 3D 面板中导入专用文件：

```text
urdf_kuavo5/urdf/biped_s300053_foxglove.urdf
```

该版本将网格路径改为 `package://urdf_kuavo5/meshes/...`，与本仓库目录一致。将 3D 面板的
Fixed frame 设为 `base_link`（或 bag 中存在的 `odom`），URDF Control mode 选择 `Transforms`。
如果 Foxglove 仍提示找不到资源，可在 Settings → ROS package paths 中加入仓库根目录：

```bash
export ROS_PACKAGE_PATH=/Volumes/yuto2/yuto/codehub/VelaLoom:$ROS_PACKAGE_PATH
```

bag 中的主体 TF（`base_link`、腿、腰、手臂和头部）与该 URDF 的 link 名称一致。相机图像原始
帧名是 `cam_*_color_optical_frame`，若还需要在 3D 面板中把相机图像叠加到机器人坐标系，先用
根目录 `scripts/modify_rosbag_camera_frames.py` 生成带相机连接 TF 的新 bag；该脚本不会修改输入 bag。

例如：

```bash
python3 scripts/modify_rosbag_camera_frames.py \
  rosbag/<input>.bag rosbag/<input>-foxglove.bag
```

该转换同时统一彩色图像和 `camera_info` 的 `frame_id`，并补充左右手相机、头部相机到机器人
TF 树的静态连接。

## 批量统一 rosbag 的 frame_id

`scripts/sync_frameid.py` 可以按完整 topic 名称修改消息的 `header.frame_id`，支持单个 bag、
多个 bag、目录批处理和递归扫描。`--input` 可以接收一个或多个文件/目录，`--output` 始终是输出
目录。输入 bag 不会被修改。`--map` 可以在一次参数中连续接收多个映射，直到下一个 `--xxx` 选项；也继续兼容重复使用 `--map`：

```bash
conda run -n VelaLoom python scripts/sync_frameid.py \
  --input rosbag/raw/a.bag rosbag/raw/b.bag rosbag/raw/more-bags \
  --output rosbag/frame-fixed \
  --recursive \
  --map /cam_l/color/image_raw/compressed=l_camera_optical_frame \
        /cam_l/color/camera_info=l_camera_optical_frame
```

也可以写成 `--map topic_a=frame_a --map topic_b=frame_b`。当前脚本没有裸子命令；映射参数会在下一个以 `--` 开头的选项处结束。

默认不会覆盖输出目录中的同名文件；冲突时会生成带 `_loom_YYYYMMDD_HHMMSS` 的文件名。使用
`--overwrite` 才会覆盖已有输出，使用 `--dry-run` 可以只扫描并查看将要修改的消息数量和输出
文件名而不写入文件。

批处理行为门禁测试位于 `tests/`，可在项目环境中运行：

```bash
conda run -n VelaLoom python -m unittest -v tests/test_sync_frameid_batch.py
```

## 统一 rosbag TF 输出

`scripts/unify_rosbag_tf.py` 仅读取单个 ROS1 bag 中已有的 `/tf` 和 `/tf_static`，去重静态变换并
完整打印 TF 森林。存在多个根时，调用者先选择目标根，再为每棵剩余树分别选择当前已合并树中的
挂载 link；脚本为每项选择增加一条单位静态变换。它不再读取 URDF，也没有写死相机 frame 或旧
head camera 规则。

```bash
conda run --no-capture-output -n VelaLoom python scripts/unify_rosbag_tf.py \
  --input rosbag/<input>.bag \
  --output test_output/issue-017/<input>-unified.bag
```

交互前和挂载后都会用 `├──`、`└──`、`│` 打印完整层级树；frame 名称末尾的 `[D]`、`[S]`、`[B]` 分别表示动态、静态或两者均有。
包含 `map`、`odom`、`base_link` 的树按该顺序获得推荐标记，但脚本不会自动替调用者选择。
挂载提示支持 `list`、`tree` 和 `abort`；挂载完成后的 `Proceed [Y/n]:` 直接回车默认写出。

`--dry-run` 仍会执行完整的选根、逐树挂载和最终拓扑验证，但不会询问写出或创建任何文件。多根
输入必须在 TTY 中交互；不提供自动 decisions 参数。默认拒绝覆盖，只有 `--overwrite` 才允许在
写后回读验证成功后原子替换输出。输入 bag 始终只读，动态 `/tf` 和非 TF 消息的原始字节、时间戳、
顺序及连接元数据保持不变，最终静态边写为一条 latched `/tf_static`。单位挂载表示调用者确认两个
frame 的原点和方向重合；脚本只验证拓扑，不能从 TF 森林推断真实几何外参。

## 将 URDF 全部 fixed joint 写入 rosbag

`scripts/add_urdf_tf_static.py` 是独立的交互式转换工具。它读取传入 URDF 中全部直接
`type="fixed"` joint，扫描输入 ROS1 bag 的 `/tf_static` 和 `/tf`，把 fixed joint 分为已存在、
缺失和冲突三类。输入 URDF 与 bag 始终只读；输出必须是另一个 `.bag` 文件。

先使用 dry-run 查看分类和冲突，不会交互、创建输出或写 decisions 文件：

```bash
conda run -n VelaLoom python scripts/add_urdf_tf_static.py \
  --input rosbag/<input>.bag \
  --output test_output/issue-016/output.bag \
  --urdf urdf_kuavo5/urdf/biped_s300053_foxglove.urdf \
  --dry-run
```

交互运行时要让 conda 保留 TTY。静态冲突可选择使用 URDF、保留 bag 或中止；动态冲突可选择
保留动态 TF，或者再次完整输入 `YES` 后删除对应动态 transform 并使用 URDF。所有冲突选择都
没有默认值。只有最终提示 `Proceed with writing OUTPUT.bag? [Y/n]` 默认 `Y`：

```bash
conda run --no-capture-output -n VelaLoom python scripts/add_urdf_tf_static.py \
  --input rosbag/<input>.bag \
  --output test_output/issue-016/output.bag \
  --urdf urdf_kuavo5/urdf/biped_s300053_foxglove.urdf \
  --decisions-out test_output/issue-016/decisions.json
```

`--decisions-out` 保存输入 bag/URDF 的 SHA-256、完整冲突候选、选择和动态删除影响。之后可在相同
输入上校验并重放；输入哈希、候选集合或影响计数变化时会失败。非交互写出还必须显式使用
`--yes`，它只能跳过最终写出确认，不能替代未解决的冲突：

```bash
conda run -n VelaLoom python scripts/add_urdf_tf_static.py \
  --input rosbag/<input>.bag \
  --output test_output/issue-016/output.bag \
  --urdf urdf_kuavo5/urdf/biped_s300053_foxglove.urdf \
  --decisions-in test_output/issue-016/decisions.json \
  --yes
```

输出把最终唯一静态边写成一条 latched `/tf_static`；未经明确选择删除的 `/tf` 和全部非 TF 消息
保持原始序列化内容、时间戳和连接元数据。默认拒绝覆盖，只有 `--overwrite` 才允许原子替换输出。
本工具不会加入 `unify_rosbag_tf.py` 的三条 `cam_h/l/r` 单位桥接，也不会自动合并
`head_rader`/`head_radar` 等相似名称。

## 根据灵巧手反馈补充动态手指 TF

`scripts/add_dexhand_tf.py` 从 ROS1 bag 的 `/dexhand/state` `JointState.position` 读取左右手各 6 个
具名反馈通道，并按 URDF 中 20 个手指 revolute joint 的 parent、child、origin、axis 和 limit 生成
动态 `/tf`。输入 bag 和 URDF 始终只读，输出必须是另一个 `.bag` 文件。

先执行完整 dry-run；它会报告反馈频率、实际名称、每个通道的范围和裁剪数、时间戳回退、20 个
目标关节、TF 冲突和预计新增消息数，但不会创建输出：

```bash
conda run -n VelaLoom python scripts/add_dexhand_tf.py \
  --input test_output/01.bag \
  --output test_output/issue-020/01_dexhand_tf.bag \
  --urdf urdf_kuavo5/urdf/biped_s300053_foxglove.urdf \
  --dry-run
```

去掉 `--dry-run` 后正式写出。默认拒绝覆盖已有输出，只有显式 `--overwrite` 才允许在临时 bag
写完并回读验证成功后原子替换。`--state-topic` 可修改反馈 topic，默认是 `/dexhand/state`。

映射使用消息中的 joint name，不依赖数组顺序。每侧 `thumb_aux → thumbCMC`、
`thumb → thumbMCP`，`index/middle/ring/pinky` 各自同时驱动对应的 MCP/PIP；反馈按
`u=clip(position/100,0,1)` 归一化，再使用 `q=lower+u(upper-lower)` 映射到 URDF 限位。左右镜像
由 URDF axis 表达，不额外反号。四指单通道同时驱动两个关节是可视化近似，不代表恢复了接触
状态下两个独立的真实角度。

每条有效反馈新增一条含 20 个 transform 的独立、非 latched `/tf` 消息；bag 时间使用反馈记录
时间，header 时间使用反馈 header，零时间回退到记录时间。目标 child 已有 TF、手掌不可从
`base_link` 到达、状态缺失/重复/非有限、TF 多 parent 或环路时会在创建输出前失败。写后回读
还会验证原始记录的序列化字节、时间戳、顺序和连接元数据没有变化。

## 生成可配置的水平合成轨迹

`scripts/loom_xy_motion.py` 把动态 `/tf` 中已有的 `odom → base_link` 水平 `x/y` 替换为一条
直线 minimum-jerk 轨迹，用于在 Foxglove 中演示指定方向、距离和时间范围的平滑移动。输入 bag
始终只读；输出不新增消息、topic、connection 或 TF 边，并保留目标变换的 `z`、旋转和时间戳，
以及所有非目标记录。

六个业务参数均为必传项。方向相对第一帧 `base_link` 姿态定义：`robot-up/down` 分别是自身
前/后，`robot-left/right` 分别是自身左/右；脚本只用第一帧 yaw 将该方向固定转换到 `odom`，
因此不会随机器人转向产生弯曲轨迹。时间可写为十进制秒、`MM:SS` 或 `HH:MM:SS`：

```bash
conda run -n VelaLoom python scripts/loom_xy_motion.py \
  --input test_output/issue-020/01_dexhand_tf.bag \
  --output test_output/issue-021/01_dexhand_tf_xy_motion.bag \
  --direction robot-up \
  --distance-m 1.0 \
  --start-s 00:02 \
  --end-s 00:12 \
  --dry-run
```

去掉 `--dry-run` 后，脚本打印输入哈希、目标样本、方向向量、起终点和理论最大速度，再以
`Proceed [Y/n]:` 确认写出。若输出已存在，TTY 中可只切换目录或只重命名并持续处理二次冲突；
非 TTY 会要求重新指定输出。工具没有 `--overwrite`：写后回读会逐记录验证连接、计数、轨迹、
非目标字节和 TF 拓扑，再通过不覆盖的原子发布生成结果。

该轨迹是明确的可视化合成数据，会覆盖原始根节点水平运动并可能产生脚底滑动。输出不得用于
定位、控制、训练或机器人性能定量评估；需要接触一致运动时应使用步态重定向和全身 IK 方案。

## 只读验证 rosbag、URDF 与关节状态的一致性

`scripts/validate_tf.py` 是通用只读验证器。它联合扫描单个 ROS1 bag 的 `/tf`、`/tf_static`、
传感器关节数组与必选 URDF，检查 TF 单根/环路/多 parent、静动态发布冲突、caller 诊断、URDF
全部 joint 的 parent/child/origin/axis/limit、源数组映射、时间延迟、整体 RMS、最大单关节误差、
连续性与速度辅助指标。脚本不修改输入，不创建或重建任何 TF，也不生成转换 bag。

默认读取 `configs/validate_tf.yaml`，当前基线是 `test_output/issue-020/01_dexhand_tf.bag` 与
Foxglove URDF。可直接执行；非 TTY 环境若存在缺失 joint，必须显式给出策略：

```bash
conda run -n VelaLoom python scripts/validate_tf.py \
  --missing-joint-policy warn \
  --json-out test_output/issue-006/report.json
```

也可完全覆盖输入、topic、字段和数组索引映射。`--joint-map` 只能出现一次，随后连续列出全部
`INDEX=JOINT_NAME`，遇到下一个 `--xxx` 选项结束；CLI 列表整体替换配置列表：

```bash
conda run -n VelaLoom python scripts/validate_tf.py \
  --bag path/to/input.bag \
  --urdf path/to/robot.urdf \
  --sensor-topic /sensors_data_raw \
  --position-field joint_data.joint_q \
  --velocity-field joint_data.joint_v \
  --timestamp-field header.stamp \
  --expected-root odom \
  --joint-map 0=joint_a 1=joint_b \
  --missing-joint-policy fail
```

参数优先级为 `CLI > 已加载 YAML > 程序默认值`。CLI 相对路径相对当前目录解析，YAML 相对路径
相对配置文件目录解析；终端报告会打印每个生效值及来源。缺失 joint 默认在 TTY 中逐项选择
Failure/Warning/Ignore/Abort，并支持 FA/WA/IA 批量应用；额外边与 fixed joint 动态发布默认告警。
planar/floating joint 会验证其允许运动空间，但不接受标量数组映射，无法证明其数据源时明确告警。

默认时间窗口为 TF 前 `30 ms`、后 `5 ms`。几何容差为 `1e-6 m` 和 `1e-5 rad`；四元数范数
容差 `1e-6`。源匹配的角度 RMS/最大门限为 `0.01/0.02 rad`，线性 RMS/最大门限为
`0.0001/0.001 m`。限位按单位分开：角度告警/失败容差为 `1e-6/0.01 rad`，线性为
`1e-6/0.001 m`。连续性默认只报告 P50/P99/最大间隔、跳变与速度，不使用机器人相关的通用硬
阈值；所有默认值均可在 YAML 的 `matching` 和 `thresholds` 中调整。角度与线性 RMS 分开报告，
同时提供按各自最大门限归一化的无量纲整体 RMS，避免直接混合 rad 与 m。

最终状态为 `PASS`、`PASS_WITH_WARNINGS` 或 `FAIL`；`--strict` 会把任何告警提升为失败。退出码
`0/1/2/3` 分别表示通过、数据验收失败、参数/配置/结构错误、调用者中止。只有指定 `--json-out`
时才写结构化报告，且默认拒绝覆盖已有文件；报告包含输入前后 SHA-256、生效配置、完整指标、
策略决定以及可证事实与告警。
