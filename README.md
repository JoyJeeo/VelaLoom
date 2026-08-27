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

`scripts/unify_rosbag_tf.py` 将单个 ROS1 bag 复制到新的输出路径，并把 `/tf_static` 重建为一条
去重、冲突检查后的 latched 消息。脚本从 Foxglove URDF 读取七条相机安装 fixed joint，加入
`camera_base → cam_h_link`、`l_d405_camera_base → cam_l_link`、`r_d405_camera_base → cam_r_link`
三条单位桥接；原始 `/tf` 和所有非 TF 消息按原始字节复制，输入 bag 始终只读。

```bash
conda run -n VelaLoom python scripts/unify_rosbag_tf.py \
  --input rosbag/<input>.bag \
  --output rosbag/unified/<input>.bag \
  --urdf urdf_kuavo5/urdf/biped_s300053_foxglove.urdf
```

默认会拒绝覆盖输出；使用 `--overwrite` 才允许覆盖，`--dry-run` 只扫描和验证而不创建输出。
若普通消息仍引用 `head_camera_base` 或 `head_camera_depth`，脚本会失败并列出 topic；确认需要
旧链时显式加 `--keep-legacy-head-chain`。

## 将 URDF 全部 fixed joint 写入 rosbag

`scripts/add_urdf_fixed_tf.py` 是独立的交互式转换工具。它读取传入 URDF 中全部直接
`type="fixed"` joint，扫描输入 ROS1 bag 的 `/tf_static` 和 `/tf`，把 fixed joint 分为已存在、
缺失和冲突三类。输入 URDF 与 bag 始终只读；输出必须是另一个 `.bag` 文件。

先使用 dry-run 查看分类和冲突，不会交互、创建输出或写 decisions 文件：

```bash
conda run -n VelaLoom python scripts/add_urdf_fixed_tf.py \
  --input rosbag/<input>.bag \
  --output test_output/issue-016/output.bag \
  --urdf urdf_kuavo5/urdf/biped_s300053_foxglove.urdf \
  --dry-run
```

交互运行时要让 conda 保留 TTY。静态冲突可选择使用 URDF、保留 bag 或中止；动态冲突可选择
保留动态 TF，或者再次完整输入 `YES` 后删除对应动态 transform 并使用 URDF。所有冲突选择都
没有默认值。只有最终提示 `Proceed with writing OUTPUT.bag? [Y/n]` 默认 `Y`：

```bash
conda run --no-capture-output -n VelaLoom python scripts/add_urdf_fixed_tf.py \
  --input rosbag/<input>.bag \
  --output test_output/issue-016/output.bag \
  --urdf urdf_kuavo5/urdf/biped_s300053_foxglove.urdf \
  --decisions-out test_output/issue-016/decisions.json
```

`--decisions-out` 保存输入 bag/URDF 的 SHA-256、完整冲突候选、选择和动态删除影响。之后可在相同
输入上校验并重放；输入哈希、候选集合或影响计数变化时会失败。非交互写出还必须显式使用
`--yes`，它只能跳过最终写出确认，不能替代未解决的冲突：

```bash
conda run -n VelaLoom python scripts/add_urdf_fixed_tf.py \
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
