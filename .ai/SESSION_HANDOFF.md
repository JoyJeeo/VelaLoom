# AI 会话交接记录

本文件用于跨 Codex 对话窗口传递上下文。每次用户明确表示结束当前对话时，AI 必须将本次对话的核心内容、技术细节、已完成工作、未完成工作和下一步动作追加到本文件；下一次用户明确表示开启新对话时，AI 必须先读取本文件并恢复上下文。

## 交接规则

- 只记录可影响后续开发的事实、决策、约束和待办，不记录无关闲聊。
- 记录具体文件路径、Issue 编号、接口名称、命令、依赖和验证结果。
- 不覆盖历史交接记录；每次新增一个日期和会话编号。
- 如果当前没有未完成工作，也要明确写出“无未完成事项”。
- 新对话接手后，将已经确认的内容视为当前上下文，不重复执行已完成的操作。

## 结束对话时使用的记录模板

```markdown
## SESSION-YYYYMMDD-NN

- 日期：YYYY-MM-DD
- 对话目标：
- 已完成：
  - 文件：
  - Issue：
  - 行为/接口：
- 技术细节：
  - 运行环境：
  - 关键命令：
  - 依赖：
- 验证结果：
- 未完成事项：
- 下一步：
- 注意事项/风险：
```

## SESSION-20260826-01

- 日期：2026-08-26
- 对话目标：建立可跨对话延续的 AI 开发协作规范。
- 已完成：
  - 将 AI 开发规范目录确定为 `.ai/`，目录内规范文件使用大写命名。
  - 使用 `.ai/TASK.md` 记录 Issue；每次开发只执行一个 Issue，不同 Issue 不允许并行执行。
  - 增加会话交接规则和本文件。
- 技术细节：
  - 项目环境：conda 环境 `VelaLoom`。
  - AI 规范入口：`.ai/README.md`。
  - 任务清单：`.ai/TASK.md`。
  - 主要工具：`scripts/sync_frameid.py`，依赖 `rosbags`。
- 验证结果：`.ai/` 文件命名和文档引用已检查，`git diff --check` 通过。
- 未完成事项：无与本次会话交接规范直接相关的未完成事项。
- 下一步：用户表示结束对话时，追加新的 `SESSION-*` 记录；新对话开始时先读取本文件。
- 注意事项/风险：结束对话记录必须在用户明确结束当前对话后执行，不要因普通暂时停顿而创建交接记录。

## SESSION-20260826-02

- 日期：2026-08-26
- 对话目标：全面测试 `scripts/sync_frameid.py` 的功能，并验证 AI 开发规范的 Issue 串行流程。
- 已完成：
  - 完成 `ISSUE-001` 并在 `.ai/TASK.md` 标记为 `DONE`。
  - 使用临时 ROS1 bag 验证单 bag、多 topic、未映射 topic、输入文件保护和消息统计。
  - 验证多个 bag 输入、目录输入、`--recursive`、`--dry-run`、默认重名保护、`_loom_时间戳` 命名和 `--overwrite`。
  - 发现并修正输出目录嵌套在递归输入目录时会重复处理生成 bag 的问题；现在会自动排除嵌套输出目录。
  - 使用仓库真实 644 MB bag 完成 dry-run，`/cam_l/color/image_raw/compressed` 扫描 1023 条，`/cam_h/color/camera_info` 扫描 1022 条。
- 技术细节：
  - 脚本：`scripts/sync_frameid.py`。
  - 环境：conda `VelaLoom`。
  - 依赖：`rosbags 0.11.5` 已安装在 `VelaLoom` 环境。
  - 测试临时目录：`/tmp/vela_sync_frameid_tests*`，未写入项目仓库。
- 验证结果：语法检查、`--help`、临时 bag 端到端读写、输入 SHA-256 不变、真实 bag dry-run 均通过。
- 未完成事项：`ISSUE-002`（正式自动化批处理测试文件）仍为 `TODO`；`ISSUE-003`（TF frame 重写模式评估）仍为 `TODO`。
- 下一步：新对话开启后，先读取本文件、`.ai/TASK.md` 和 `.ai/PROGRESS.md`；应按串行规则选择一个 Issue，不能并行执行多个 Issue。
- 注意事项/风险：真实 bag 仅做了 dry-run，未对 644 MB 原始 bag 执行实际写出；实际转换应输出到独立目录并保留原始文件。

## SESSION-20260826-03

- 日期：2026-08-26
- 对话目标：开始并完成 `ISSUE-003`，评估 `/tf` 与 `/tf_static` 的 frame 重写模式。
- 已完成：
  - 文件：`.ai/TASK.md`、`.ai/DECISIONS.md`、`.ai/PROGRESS.md`、`CHANGELOG.md`、`tests/test_sync_frameid_batch.py`。
  - Issue：`ISSUE-003` 已标记为 `DONE`。
  - 决策：`sync_frameid.py` 暂不提供通用 `/tf`、`/tf_static` frame 重写；未来如需支持，必须另建独立模式和独立映射/TF 树验证。
  - 测试：新增 TF 安全边界回归测试，验证普通映射遇到 `/tf` 会失败、清理临时输出且保持输入 SHA-256 不变。
- 技术细节：
  - 真实 rosbag 结构：`/tf` 56,292 条消息、30 对动态 frame；`/tf_static` 20 条消息、32 对静态 frame。
  - 环境：conda `VelaLoom`，依赖 `rosbags`。
  - 开发分支：`codex/issue-003-tf-frame-rewrite`；按约定尚未在特性分支提交或推送。
- 验证结果：`conda run -n VelaLoom python -m unittest discover -s tests -v` 通过（6 项）；Python 语法检查和 `git diff --check` 通过。
- 未完成事项：本次 ISSUE-003 修改尚未提交到 `main` 或推送；本地 `logs/` 目录保持未跟踪，未纳入交付。
- 下一步：下次对话如继续，应先核对 `git status`，按流程将 ISSUE-003 相关修改提交到 `main` 并推送；若要实现 TF 改写，应新建独立 Issue。
- 注意事项/风险：当前拒绝 TF 普通映射是刻意的安全行为，不要直接放宽为字符串替换；需先定义 parent/child 映射、冲突检测、动态/静态时间语义和完整 TF 树验证。

## SESSION-20260827-01

- 日期：2026-08-27
- 对话目标：确定 URDF 与 rosbag TF 树的统一方式，并把 ROS1 Docker 执行约定和统一 TF 输出 bag 脚本方案写入本地 AI 开发规则与 Issue。
- 已完成：
  - 文件：`AGENTS.md`、`.ai/README.md`、`.ai/PROJECT_CONTEXT.md`、`.ai/DEVELOPMENT_WORKFLOW.md`、`.ai/TESTING.md`、`.ai/DECISIONS.md`、`.ai/PROGRESS.md`、`.ai/TASK.md`、`CHANGELOG.md`。
  - Issue：新增 `ISSUE-015：实现统一 TF 输出 bag 转换脚本`，状态为 `TODO`，目标脚本为 `scripts/unify_rosbag_tf.py`。
  - 方案：URDF 保持不变；原始 bag 保持不变；输出 bag 原样保留动态 `/tf`（包括 `odom → base_link`），重建去重后的 `/tf_static`，补齐 URDF 相机固定安装边，并增加三条单位桥接：`camera_base → cam_h_link`、`l_d405_camera_base → cam_l_link`、`r_d405_camera_base → cam_r_link`。
  - 头部策略：输出结构采用 `zhead_2_link → camera_base → cam_h_link`；旧的 `zhead_2_link → head_camera_base → head_camera_depth` 链需要先扫描消息引用，不能静默删除。
  - Foxglove 验收思路：输出 bag 提供实际 TF，URDF 作为 3D custom layer 以 `Transforms` 模式加载，Fixed frame 使用 `odom`；Transform Tree 只能看到数据源实际发布的 TF，不能自动显示 URDF 中缺失的边。
- 技术细节：
  - ROS1 执行环境：需要 ROS Noetic runtime 的 ROS/rosbag/ROS 消息/节点/构建/测试命令统一在 Docker 容器 `ros1_noetic` 中执行；非 ROS 命令使用 conda `VelaLoom`。
  - 使用容器前需 `docker inspect ros1_noetic` 确认宿主机工作区挂载；容器停止时先启动并加载 `/opt/ros/noetic/setup.bash`；输出写入挂载路径。
  - 真实 bag 与 URDF 的结构基线：URDF 75 条 joint，bag 62 条唯一 TF 边，44 条父子边完全匹配；bag 根为 `odom`、`cam_h_link`、`cam_l_link`、`cam_r_link`。
  - 相关决策：`.ai/DECISIONS.md` 中新增 D008（ROS1 Docker）和 D009（统一 TF 输出 bag 及单位桥接）。
- 验证结果：`conda run -n VelaLoom git diff --check` 通过；本次只修改规则、规划和记录文件，没有创建脚本，没有修改 URDF、原始 rosbag 或测试代码。
- 未完成事项：`ISSUE-005` 仍为 `TODO`；`ISSUE-015` 尚未开始；没有生成统一 TF 输出 bag，也没有在 `ros1_noetic` 容器中执行 ROS 回放或 Foxglove 验收。
- 下一步：新对话开启后先读取本文件、`.ai/TASK.md`、`.ai/PROGRESS.md`，确认 `ISSUE-005`/`ISSUE-015` 串行依赖；开始 ROS 操作前检查 `ros1_noetic` 挂载路径，再按 ISSUE-015 阶段一设计并实现 `scripts/unify_rosbag_tf.py`。
- 注意事项/风险：三条单位桥接是用户确认的拓扑方案，但仍代表坐标系重合假设；头部深度图当前使用 `cam_h_color_optical_frame`、腰部相机命名、雷达拼写以及 12→20 手指关节映射尚未解决；工作区既有 `.ai/*` 修改、`logs/` 和 `biped_s300053_foxglove_副本.urdf` 删除状态必须保留，不能误覆盖或纳入无关提交。

## SESSION-20260827-02

- 日期：2026-08-27
- 对话目标：完成 ISSUE-015 的统一 TF 输出 bag 脚本和真实数据验证；澄清 Foxglove/URDF/TF 发布关系；规划独立的全量 URDF fixed joint 交互写入脚本；统一测试输出目录；提交并推送仓库全部既有改动。
- 已完成：
  - 文件：新增 `scripts/unify_rosbag_tf.py`、`tests/test_unify_rosbag_tf.py`；更新 `README.md`、`CHANGELOG.md`、`.ai/*`、`AGENTS.md`、`.gitignore`；删除重复文件 `urdf_kuavo5/urdf/biped_s300053_foxglove_副本.urdf`；提交 3 个 `logs/gym-mcp.*.log.gz` 压缩日志。
  - Issue：`ISSUE-015` 已实现并标记为 `DONE`；新增 `ISSUE-016：将 URDF 全部 fixed joint 交互式写入 rosbag`，状态为 `TODO`，本次未实现该脚本。
  - ISSUE-015 行为：保持动态 `/tf` 和非 TF 消息原始序列化内容，去重并重建单条 latched `/tf_static`，从 URDF 读取 7 条相机 fixed joint，加入三条 `cam_h/l/r` 单位桥接，执行旧头部链引用、位姿冲突、多 parent 和唯一 `odom` 根检查。
  - ISSUE-016 方案：目标脚本为 `scripts/add_urdf_fixed_tf.py`，与已有脚本无导入或相机桥接依赖；读取 URDF 全部 fixed joint；冲突由调用者交互选择；最终提示为 `Proceed with writing OUTPUT.bag? [Y/n]` 且 Enter 默认 `Y`；冲突选择没有默认值；支持 decisions JSON 保存和校验重放。
  - 测试输出规范：所有测试夹具、临时 bag、转换输出、日志、报告等统一写入仓库根目录 `test_output/`，按 Issue/模块分子目录；`/test_output/` 已加入 `.gitignore`；禁止未来测试使用系统 `/tmp` 或输入数据目录。
- 技术细节：
  - 运行环境：非 ROS 工具和测试使用 conda `VelaLoom`；依赖继续使用 `rosbags 0.11.5`。
  - 指定只读输入：`rosbag/A03-A22-H-C-01-004-5_140-dex_hand-20260820190611-53-3ea2cb-v003.bag` 与 `urdf_kuavo5/urdf/biped_s300053_foxglove.urdf`。
  - 真实 ISSUE-015 输出：输入 149,790 条消息；输出 149,771 条；20 条原始 `/tf_static` 消息规范化为 1 条、含 40 条唯一静态边；动态 `/tf` 保持 56,292 条消息和 30 条边；最终根集合为 `{odom}`。
  - 输入 SHA-256 前后均为 `8a527e4811fc0a078f107670dd35e3c7b35d45fe7de0d94b5ee88c69a8e542ed`。
  - 全量 fixed 只读基线：URDF 有 26 条 fixed joint；15 条与 bag 已有边一致，10 条为缺失新边，1 条结构冲突为 URDF `waist_yaw_link → waist_camera` 对 bag `waist_camera_base → waist_camera`；`head_rader`/`head_radar` 仅作为相似名称警告。
  - ROS1 容器：`ros1_noetic` 绑定源 `/Volumes/yuto2` 在当前宿主机不存在，容器无法启动；因此没有完成原生 `rosbag info` 和 Foxglove 人工回放，已用 `rosbags` 完成真实输出回读和 TF 树检查。
- 验证结果：最终在 `test_output/` 下运行完整测试，共 15 项通过；Python 语法检查和 `git diff --check` 通过；原始 bag 和 URDF 未修改。
- 提交和推送：`71a146e feat: add unified rosbag TF conversion`、`27b18fb docs: update AI workflow and TF issue plans`、`b1d7220 chore: clean duplicate URDF and archive logs` 已推送到 `origin/main`；推送后本地与远端均指向 `b1d7220`。
- 未完成事项：`ISSUE-016` 尚未开始开发；`ISSUE-005` 至 `ISSUE-014` 中除 ISSUE-015 外仍按 `.ai/TASK.md` 状态等待；ROS1 原生和 Foxglove 人工验收等待容器挂载修复。
- 下一步：新对话开始后先读取本文件、`.ai/TASK.md` 和 `.ai/PROGRESS.md`；若用户要求继续，应按串行规则将 ISSUE-016 标为 `IN_PROGRESS`，创建独立模块并严格把所有测试输出写入 `test_output/issue-016/`。
- 注意事项/风险：ISSUE-016 不得复用 ISSUE-015 的三条相机单位桥接；任何 static/dynamic、位姿或多 parent 冲突都必须由调用者交互或匹配当前输入哈希的 decisions 文件明确决定，脚本不得自动选择；最终写出确认默认 `Y` 只适用于写出步骤，不适用于冲突选择。
