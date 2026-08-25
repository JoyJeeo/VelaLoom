# 项目上下文

## 项目定位

VelaLoom 是机器人多模态数据流水线工具集，用于将原始机器人数据转换为结构化、标准化的数据集，并提供 rosbag、URDF 和 Foxglove 相关工具。

## 执行环境

- 项目命令统一在 conda 环境 `VelaLoom` 中执行。
- 非交互命令优先使用 `conda run -n VelaLoom <command>`。
- rosbag 工具使用 Python 包 `rosbags`。
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
