# 开发进展

更新时间：2026-08-26

## 已完成

- 增加 ROS1 执行环境约定：需要 ROS Noetic runtime 的 ROS/rosbag 操作使用 `ros1_noetic` Docker 容器，非 ROS 命令使用 `VelaLoom` conda 环境；容器仅作为运行载体，输入输出通过宿主机工作区挂载保存。
- 新增 `ISSUE-015` 规划：实现统一 TF 输出 bag 转换脚本；URDF 和原始 bag 保持不变，输出 bag 去重 `/tf_static`、补齐相机安装固定边，并使用三条已确认的单位桥接接入 `cam_h/l/r_link`。
- 增加 Foxglove URDF 说明和模型文件；
- 将 rosbag 相机处理脚本整理到根目录 `scripts/`；
- 新增 `scripts/sync_frameid.py`，支持单个/多个 bag、目录、递归、`--dry-run`、`--overwrite` 和重名保护；
- 在 `VelaLoom` 环境安装 `rosbags`；
- 建立 `.ai/` AI 开发规范目录和 Issue 任务管理。
- 完成 `ISSUE-001`：使用临时 ROS1 bag 完成 `sync_frameid.py` 端到端验证，并修正递归输入目录包含输出目录时的重复处理问题。
- 完成 `ISSUE-002`：新增 `tests/test_sync_frameid_batch.py` 作为批处理功能门禁，覆盖多输入路径去重、递归目录、dry-run、默认重名保护和 `--overwrite`。

## ISSUE-002 验证记录（2026-08-26）

- 变更文件：`tests/test_sync_frameid_batch.py`、`.ai/TASK.md`。
- 验证命令：`conda run -n VelaLoom python -m unittest -v tests/test_sync_frameid_batch.py`。
- 结果：3 个测试全部通过；测试夹具生成的输入 bag 在转换后 SHA-256 保持不变，dry-run 不创建输出目录，默认冲突保护和显式覆盖行为符合预期。

## DOD 复核记录（2026-08-26）

- 复核对象：`scripts/sync_frameid.py` 及其批处理功能。
- 初次复核发现：原有门禁覆盖成功路径和批处理边界，但缺少非法映射、输入路径不存在等失败路径自动化断言。
- 补强变更：`tests/test_sync_frameid_batch.py` 新增 2 个失败路径测试。
- 验证命令：`conda run -n VelaLoom python -m unittest discover -s tests -v`、`conda run -n VelaLoom python -m py_compile scripts/sync_frameid.py tests/test_sync_frameid_batch.py`、`git diff --check`。
- 结果：5 个测试全部通过，语法检查和 diff 检查通过；当前 ISSUE-001/002 的适用 DOD 条件满足。真实大 bag 仅保留既有 dry-run 记录，未执行大规模实际写出。

## 待完成

- 评估是否需要支持 `/tf` 与 `/tf_static` 的 frame 重写（ISSUE-003）。

## rosbag ↔ URDF 映射专项基线（2026-08-26）

- 分析对象：`urdf_kuavo5/urdf/biped_s300053_foxglove.urdf` 与 `rosbag/A03-A22-H-C-01-004-5_140-dex_hand-20260820190611-53-3ea2cb-v003_loom_20260826_113517.bag`。
- 当前结论：URDF 有 75 条 joint；bag 有 62 条唯一 parent→child TF 边，其中 44 条与 URDF 完全匹配，31 条 URDF 边未出现在 bag，18 条 bag 边为额外或命名不同。
- 已确认问题：
  - bag 的 TF 根为 `odom`、`cam_h_link`、`cam_l_link`、`cam_r_link`，不是单一机器人树；
  - `/tf` 只包含身体 29 条动态链，缺少双手掌/手指和双手 D405 相机链；
  - `/dexhand/state` 只有 12 个通道，无法唯一恢复 URDF 的 20 个手指 revolute joint；
  - `camera_base`/`head_camera_base`、`head_rader`/`head_radar`、`waist_camera`/`waist_camera_base` 存在命名或层级差异；
  - 同一 bag 内图像 header 同时使用 URDF frame 和孤立 `cam_*_optical_frame`，头部深度还使用 `cam_h_color_optical_frame`；
  - `/tf_static` 的重复静态消息数值一致，未发现同一 parent→child 的冲突位姿。
- 处理原则：将 10 条修复建议拆成 ISSUE-005 至 ISSUE-014，严格按编号串行讨论和实现；本阶段不修改 URDF、rosbag 或程序代码。

## 开发流程规范整改记录（2026-08-26）

- 新增根目录 `CHANGELOG.md`，作为每个特性或修复必需的交付记录，记录变更内容和原因；它不替代测试门禁。
- 更新 `DEVELOPMENT_WORKFLOW.md`、`DOD.md`、`TESTING.md` 和 `.ai/README.md`：增加单模块变更边界、特性分支隔离、仅在 `main` 提交/推送、模块文件头 Purpose/Input/Output/Example、测试与回归门禁及无 `TODO` 交付要求。
- 验证：`git diff --check` 通过；本次仅修改开发规范和变更记录，未创建分支、提交或推送，保留工作区既有本地变动。

## 测试门禁边界修正（2026-08-26）

- 将 `CHANGELOG.md` 明确定义为发布和交付追踪材料，不再作为功能正确性或测试通过的门禁项。
- 修正 DOD：`CHANGELOG.md` 必须出现在每个特性/修复的交付记录中，但仍不作为测试通过的依据。

## 阶段化开发规范（2026-08-26）

- 更新 `DEVELOPMENT_WORKFLOW.md`、`DOD.md`、`TESTING.md`：要求每个 Issue 拆分为连续阶段，每阶段完成最小实现后立即测试；测试通过自动推进下一阶段，失败则停止推进并修复，禁止全部写完后再集中测试。

## ISSUE-003 验证记录（2026-08-26）

- 阶段一：读取真实 rosbag 结构，确认 `/tf` 有 56,292 条消息、30 对动态 frame，`/tf_static` 有 20 条消息、32 对静态 frame；通过。
- 阶段二：新增 `tests/test_sync_frameid_batch.py::test_tf_topic_is_rejected_without_a_dedicated_mode`，验证 TF 普通映射失败、临时输出清理和输入 SHA-256 不变；通过。
- 阶段三：全量测试、语法检查和 `git diff --check` 通过；结论为暂不新增通用 TF 重写模式，详见 `DECISIONS.md` D005。
- 明确真正的功能门禁是自动化测试、集成/端到端测试，以及适用的语法、静态、构建和数据安全检查。

## 更新规则

每完成一个 Issue，追加日期、Issue 编号、变更文件、验证命令、结果和遗留问题。保留历史记录，不删除已完成条目。

## ISSUE-015 测试记录（2026-08-27）

- 测试等级：L3
- 变更范围：新增 `scripts/unify_rosbag_tf.py`；新增 `tests/test_unify_rosbag_tf.py`；同步 `README.md`、`DECISIONS.md`、`TASK.md`、`CHANGELOG.md`。
- 测试夹具：运行时生成最小 ROS1 bag，覆盖静态重复边、冲突 parent、旧头部 frame 引用、输出冲突和原子失败清理；真实测试输入为仓库中的 `rosbag/A03-A22-H-C-01-004-5_140-dex_hand-20260820190611-53-3ea2cb-v003.bag` 与 `urdf_kuavo5/urdf/biped_s300053_foxglove.urdf`，两者只读。
- 执行环境：非 ROS 测试使用 `VelaLoom` conda 环境（`rosbags 0.11.5`）；ROS1 容器检查发现 `ros1_noetic` 因宿主挂载源 `/Volumes/yuto2` 不存在而无法启动。
- 验证命令：
  - `conda run -n VelaLoom python -m unittest -v tests/test_unify_rosbag_tf.py`；
  - `conda run -n VelaLoom python -m unittest discover -s tests -v`；
  - `conda run -n VelaLoom python -m py_compile scripts/unify_rosbag_tf.py tests/test_unify_rosbag_tf.py`；
  - `conda run -n VelaLoom python scripts/unify_rosbag_tf.py --input <真实 bag> --output /tmp/vela-issue15-output-XXXXXX.bag --urdf <Foxglove URDF>`；
  - 输出 bag 使用 `rosbags` 重新读取并检查消息、TF 边和根集合。
- 结果：通过。真实 bag 输出 149,771 条消息（输入 149,790 条，20 条重复 `/tf_static` 合并为 1 条），规范化 `/tf_static` 为 40 条唯一边，动态 `/tf` 仍为 56,292 条消息和 30 条边，TF 根集合为 `{odom}`；输入 SHA-256 前后均为 `8a527e4811fc0a078f107670dd35e3c7b35d45fe7de0d94b5ee88c69a8e542ed`。
- 覆盖行为：URDF fixed joint 读取、静态去重、位姿/多 parent 冲突失败、三条单位桥接、默认旧链安全失败、显式保留旧链、动态和非 TF 原样复制、dry-run、默认覆盖保护、临时输出清理。
- 未覆盖行为和风险：由于 `ros1_noetic` 的宿主挂载路径失效，未能运行原生 `rosbag info` 或 Foxglove 人工回放；已用 `rosbags` 完成等价的输出可读性、消息数量、原始动态 TF、唯一根和输入哈希检查。三条单位桥接仍是用户确认的坐标重合假设；头部深度 header、雷达命名、腰部相机命名和手指动态 TF 不在本 Issue 范围内。
- 遗留动作：修复宿主机 `/Volumes/yuto2` 挂载后，可补跑 `ros1_noetic` 中的 `rosbag info` 与 Foxglove 人工验收；不影响本脚本自动化门禁。

## ISSUE-004 验证记录（2026-08-26）

- 阶段一（方案与测试）：新增参数解析测试，先验证单次多映射和重复 `--map` 兼容行为；初始测试按预期失败。
- 阶段二（最小实现）：`scripts/sync_frameid.py` 使用 `action="append", nargs="+"`，并在业务逻辑入口展开映射分组；遇到下一个 `--xxx` 选项即结束当前映射组。
- 阶段三（集成与回归）：新增单次映射组改写两个 topic 的 rosbag 夹具测试；完整测试套件共 9 项通过。
- 验证命令：`conda run -n VelaLoom python -m unittest discover -s tests -v`、`conda run -n VelaLoom python -m py_compile scripts/sync_frameid.py tests/test_sync_frameid_batch.py`、`conda run -n VelaLoom python scripts/sync_frameid.py --help`、`git diff --check`。
- 结果：全部通过；未新增依赖，输入只读和既有 TF 安全边界保持不变。
- 变更文件：`scripts/sync_frameid.py`、`tests/test_sync_frameid_batch.py`、`README.md`、`.ai/TASK.md`、`.ai/DECISIONS.md`、`.ai/PROGRESS.md`、`CHANGELOG.md`。
- 限制：当前 CLI 无裸子命令，映射边界定义为下一个以 `--` 开头的选项；未来新增子命令时需单独设计解析边界。
