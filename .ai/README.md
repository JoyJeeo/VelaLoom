# VelaLoom AI 开发规范

`.ai/` 是 VelaLoom 面向 AI 开发助手的协作入口。这里记录项目上下文、开发流程、架构约束、任务 Issue、技术决策和开发进展。

## AI 开始任务时的阅读顺序

1. `PROJECT_CONTEXT.md`：项目目标、目录职责和执行环境。
2. `DEVELOPMENT_WORKFLOW.md`：Issue 接收、实现和交付规范。
3. `DOD.md`：判断一个功能是否可以标记为完成。
4. `TESTING.md`：测试门禁、回归防护和功能兜底规范。
5. `ARCHITECTURE.md`：项目框架和模块边界。
6. `TASK.md`：选择要执行的 Issue。
7. `PROGRESS.md`：了解已完成工作和当前待办。
8. `SESSION_HANDOFF.md`：恢复上一个对话窗口的交接信息。

## 核心规则

- 每次开发只执行一个 Issue；不得在一个开发过程中混入多个未关联目标。
- 一个特性只修改一个模块；测试、文档和变更日志只能作为该模块的配套变更。
- 每个特性先在 `codex/<issue>-<short-name>` 分支开发和验证；不在特性分支提交或推送，最终只在 `main` 提交并按授权推送 `main`。
- 不同 Issue 不可以并行执行；项目同一时刻只允许一个 Issue 处于开发执行状态。
- 非 ROS 命令使用 `VelaLoom` conda 环境；需要 ROS1/ROS Noetic runtime 的 ROS、rosbag 和相关构建/测试命令使用 `ros1_noetic` Docker 容器，并通过挂载目录保留宿主机输入输出。
- 所有开发测试生成的夹具、临时文件、bag、日志、报告和其他文件输出统一写入仓库根目录 `test_output/`；不得写入系统 `/tmp`、仓库外路径、`rosbag/` 或 `urdf*/`。具体规则见 `TESTING.md`。
- 开发开始时将对应 Issue 标为 `IN_PROGRESS`，完成或阻塞时更新状态。
- 新增或修改技术决策时同步更新 `DECISIONS.md`。
- 交付后同步更新 `PROGRESS.md`。
- 用户明确结束当前对话时，更新 `SESSION_HANDOFF.md`；用户明确开启新对话时，先读取该文件再接手任务。

## 文件命名

`.ai/` 下的规范文件统一使用大写文件名：`README.md`、`PROJECT_CONTEXT.md`、`DEVELOPMENT_WORKFLOW.md`、`DOD.md`、`TESTING.md`、`ARCHITECTURE.md`、`TASK.md`、`PROGRESS.md`、`DECISIONS.md`、`SESSION_HANDOFF.md`。
