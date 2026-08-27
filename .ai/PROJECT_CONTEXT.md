# 项目上下文

## 项目定位

VelaLoom 是机器人多模态数据流水线工具集，用于将原始机器人数据转换为结构化、标准化的数据集，并提供 rosbag、URDF 和 Foxglove 相关工具。

## 执行环境

- 非 ROS 项目命令统一在 conda 环境 `VelaLoom` 中执行；非交互命令优先使用 `conda run -n VelaLoom <command>`。
- 需要 ROS1/ROS Noetic 运行时的 ROS、rosbag、`rosrun`、ROS 构建或消息工具，统一在 Docker 容器 `ros1_noetic` 中执行。
- `ros1_noetic` 只作为指令运行载体；实际输入、输出、日志和生成文件必须通过宿主机工作区挂载目录读写，不能只留在容器内部。
- 使用容器前先用 `docker inspect ros1_noetic` 确认工作区挂载；容器停止时先启动，并在命令中加载 `/opt/ros/noetic/setup.bash`。
- `rosbags` 等纯 Python 工具若不依赖 ROS1 runtime，可在 `VelaLoom` 中执行；一旦命令需要 ROS Noetic 或 ROS1 消息运行环境，切换到容器。
- 不要在 `base` 或系统 Python 中安装项目依赖。

## 目录约束

- `scripts/`：项目共享脚本和可执行工具。
- `urdf_kuavo5/urdf/`：机器人描述和模型资产，不放开发脚本。
- `rosbag/`：原始或生成的记录数据。
- `assets/`：静态资源。
- `.ai/`：AI 开发规范、Issue、架构、决策和进展文档。

## 关键不变量

- rosbag 转换工具默认不得修改输入 bag。
- 文件批处理默认不得覆盖已有输出。
- topic 匹配使用完整名称，避免模糊匹配误改消息。
- 修改坐标关系前，必须区分普通消息的 `header.frame_id` 与 TF 的 `child_frame_id`。
