# 开发进展

更新时间：2026-08-27

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

## ISSUE-016 测试记录（2026-08-27）

- 测试等级：L3。
- 变更范围：新增独立模块 `scripts/add_urdf_tf_static.py` 和 `tests/test_add_urdf_tf_static.py`；同步 `README.md`、`DECISIONS.md`、`TASK.md` 和 `CHANGELOG.md`。没有导入或修改 ISSUE-015 脚本，没有修改输入 URDF 或 rosbag。
- 阶段一：冻结 CLI、全量 fixed joint 解析、冲突选项、最终 `[Y/n]` 默认值和 decisions schema；指定真实 URDF/bag 只读复核为 26 条 fixed、15 条一致、10 条缺失、1 条腰部 parent 冲突。
- 阶段二：实现 URDF fixed 解析和纯内存 bag 分析；4 项测试覆盖缺失、一致、位姿冲突、不同 parent、bag 多 parent、动态 child、非法数值和 URDF 多 parent。
- 阶段三：实现无默认冲突提示、动态删除完整 `YES` 二次确认、默认 `Y` 最终确认、EOF 安全中止和 decisions 保存/重放；8 项累计测试通过，哈希或候选集合变化会拒绝重放。
- 阶段四：实现唯一 latched `/tf_static`、显式动态 transform 删除、同目录临时 bag、回读验证和原子替换；12 项模块测试通过，验证输入 SHA-256、未修改动态/非 TF 原始字节、连接元数据、覆盖保护及失败清理。
- 阶段五：对 `rosbag/A03-A22-H-C-01-004-5_140-dex_hand-20260820190611-53-3ea2cb-v003.bag` 和 `urdf_kuavo5/urdf/biped_s300053_foxglove.urdf` 执行真实 dry-run；调用者对 `waist_camera` 冲突选择 `keep_bag`，保留 `waist_camera_base → waist_camera`，随后完成交互写出和 decisions 非交互重放。
- 真实输出：`test_output/issue-016/real-output.bag`（Git 忽略）含 149,771 条消息和单条 latched `/tf_static`，其中 42 条静态 transform；URDF fixed 覆盖率 25/26；未删除或修改动态 transform。`test_output/issue-016/real-decisions.json` 保存 `waist_camera=keep_bag` 及输入绑定摘要。
- 数据安全：输入 bag SHA-256 在验证后仍为 `8a527e4811fc0a078f107670dd35e3c7b35d45fe7de0d94b5ee88c69a8e542ed`，URDF SHA-256 为 `ba49bc66b484da17bee2a3b48444ada914e9252bf9b396009a36c13dcb9532e5`。独立流哈希确认 56,292 条 `/tf` 和 93,478 条非 TF 消息的原始字节、时间戳及连接元数据保持不变。
- TF 验证：输出静态 child 唯一；合并动态/静态后 72 个唯一 child 均只有一个 parent。根集合为 `{odom, cam_h_link, cam_l_link, cam_r_link}`，符合本 Issue 明确不加入 ISSUE-015 三条相机单位桥接的边界；`head_rader`/`head_radar` 仅警告且没有自动合并。
- 最终门禁：`TMPDIR="$PWD/test_output/issue-016/tmp" PYTHONPYCACHEPREFIX="$PWD/test_output/issue-016/pycache" conda run -n VelaLoom python -m unittest discover -s tests -v` 通过（27 项）；全部脚本/测试 `py_compile`、`add_urdf_tf_static.py --help` 和 `git diff --check` 通过。
- 测试产物：保留 `test_output/issue-016/real-output.bag` 和 `real-decisions.json` 供人工复核；精确清理本次 `tmp/` 与 `pycache/` 子目录，不清理其他 `test_output/` 内容。
- 依赖和限制：未新增依赖，继续使用 `VelaLoom` 环境中的 `rosbags 0.11.5`。本工具不建立 `cam_h/l/r` 相机桥接，因此这三棵相机树仍是独立根；这是 ISSUE-016 的明确范围，不是未解释失败。

## ISSUE-017 测试记录（2026-08-28）

- 测试等级：L3。
- 变更范围：重构 `scripts/unify_rosbag_tf.py` 和 `tests/test_unify_rosbag_tf.py`；同步 `README.md`、`DECISIONS.md`、`TASK.md` 和 `CHANGELOG.md`。没有修改其他转换脚本、URDF 或输入 rosbag。
- 阶段一至二：先写新 CLI、扫描、去重、拓扑和完整森林日志回归测试，再实现 bag-only 分析；6 项测试通过。删除 `--urdf`、`--keep-legacy-head-chain`、URDF fixed joint、写死相机桥接和旧 head camera 专用规则。
- 阶段三：实现目标根编号/名称选择、逐树挂载、增长中的目标树、`list/tree/abort`、无效 parent 重试和非 TTY 安全失败；累计 11 项测试通过。
- 阶段四：实现 `Proceed [Y/n]:` 默认写出、取消/EOF、dry-run、同目录唯一临时 bag、回读验证和原子替换；累计 18 项测试通过。覆盖默认/显式覆盖、输入输出同路径、非法后缀、强制验证失败清理，以及动态 `/tf` 和非 TF 原始记录保真。
- 阶段五真实验证：输入 `rosbag/A03-A22-H-C-01-004-5_140-dex_hand-20260820190611-53-3ea2cb-v003.bag` 只读扫描得到 149,790 条消息、56,292 条 `/tf`、20 条 `/tf_static`、93,478 条非 TF；352 条输入静态 transform 去重为 32 条，联合 30 条动态唯一边形成 `cam_h_link`、`cam_l_link`、`cam_r_link`、`odom` 四棵树。
- 真实交互选择：目标根选择 `odom`；调用者确认 `zhead_2_link → cam_h_link`、`zarm_l7_link → cam_l_link`、`zarm_r7_link → cam_r_link` 三条单位静态边。dry-run 和正式写出均完成，修复后为单一 `odom` 根、66 个 frame、无环且每个 child 单 parent。
- 真实输出：保留 Git 忽略的 `test_output/issue-017/real-output.bag` 供人工复核，大小约 644 MB；回读为 149,771 条消息、单条 latched `/tf_static`（35 条 transform）、56,292 条动态 TF 和 93,478 条非 TF 消息。脚本内回读逐条确认动态/非 TF 的原始字节、时间戳、顺序和连接元数据保持不变。
- 数据安全：输入 SHA-256 在 dry-run、写出和独立复核后均为 `8a527e4811fc0a078f107670dd35e3c7b35d45fe7de0d94b5ee88c69a8e542ed`；没有残留 `.real-output.*.tmp.bag`。
- 最终门禁：`TMPDIR="$PWD/test_output/issue-017/tmp" PYTHONPYCACHEPREFIX="$PWD/test_output/issue-017/pycache" conda run -n VelaLoom python -m unittest discover -s tests -v` 通过（39 项）；全部脚本和测试 `py_compile`、`unify_rosbag_tf.py --help`、交付代码无 `TODO` 和 `git diff --check` 通过。
- 测试产物：所有本次生成物均位于 `test_output/issue-017/`；保留 `real-output.bag`，最终精确清理本次 `tmp/` 和 `pycache/` 子目录，不清理其他 Issue 的输出。
- 依赖和限制：未新增依赖，继续使用 `VelaLoom` 环境中的 `rosbags 0.11.5`。未调用 ROS Noetic 命令，因此无需启动 `ros1_noetic`；真实格式、拓扑和保真验证均由 `rosbags` 完成。单位挂载是调用者确认的坐标重合假设，不代表脚本恢复了几何标定外参。

## ISSUE-018 测试记录（2026-08-28）

- 测试等级：L1；仅调整 `scripts/unify_rosbag_tf.py` 终端展示格式及对应测试、README 和交付记录，不改变 TF 数据结构、交互决策或 bag 写出行为。
- 行为：完整森林、独立子树和修复后树统一显示为 `frame [D/S/B]`；新增单位边摘要显示为 `parent -> child [S]`；同级 frame 仍按名称稳定排序。
- 回归测试：先更新后缀格式断言，确认旧实现有 3 项失败；实现后模块 18 项测试和全仓 39 项测试全部通过，同时覆盖 `[D]`、`[S]`、`[B]` 三种来源。
- 验证命令：`TMPDIR="$PWD/test_output/issue-018/tmp" PYTHONPYCACHEPREFIX="$PWD/test_output/issue-018/pycache" conda run -n VelaLoom python -m unittest discover -s tests -v`、全部脚本/测试 `py_compile`、`git diff --check`。
- 测试产物：本轮生成文件仅位于 `test_output/issue-018/`，最终精确清理 `tmp/` 和 `pycache/`；未生成或修改真实 bag。
- 依赖和限制：未新增依赖；本次仅改变终端日志布局。

## ISSUE-019 测试记录（2026-08-28）

- 测试等级：L1；仅调整 `scripts/unify_rosbag_tf.py` 的树形终端布局以及对应测试、README 和交付记录，不改变 TF 拓扑、交互选择或 bag 内容。
- 行为：根节点保持独立首行；中间同级节点使用 `├──`，最后节点使用 `└──`，祖先仍有后续同级分支时用 `│` 纵向贯穿；frame 来源继续显示在名称末尾。
- 回归测试：先增加连接线和多层分支断言，确认旧空格缩进实现失败；实现后模块 18 项与全仓 39 项测试通过，同级名称稳定排序保持不变。
- 真实只读预览：对仓库真实 bag 的 `odom` 树执行 `format_subtree`，确认腿部、腰部、双臂和头部分支均有连续连接线；未写出或修改 bag。
- 验证命令：`TMPDIR="$PWD/test_output/issue-019/tmp" PYTHONPYCACHEPREFIX="$PWD/test_output/issue-019/pycache" conda run -n VelaLoom python -m unittest discover -s tests -v`、全部脚本/测试 `py_compile`、`git diff --check`。
- 测试产物：本轮生成文件仅位于 `test_output/issue-019/`，最终精确清理 `tmp/` 和 `pycache/`。
- 依赖和限制：未新增依赖；终端需支持 Unicode 线框字符才能按设计显示。

## ISSUE-020 测试记录（2026-08-29）

- 测试等级：L3。
- 变更范围：新增独立模块 `scripts/add_dexhand_tf.py` 和 `tests/test_add_dexhand_tf.py`；同步 README、DECISIONS、TASK、PROGRESS 和 CHANGELOG；未修改其他转换脚本、URDF 或输入 bag。
- 阶段一至二：冻结 CLI、12→20 名称映射、错误策略、时间戳和输出不变量；实现 URDF 目标 revolute joint 解析、`0/50/100` 限位映射、axis 归一化和 `T_origin*R_axis(q)`，7 项阶段测试通过。
- 阶段三：实现状态 topic、TF 和完整输入 bag 只读扫描；覆盖状态缺失/重复/非有限/时间非法、目标 child 冲突、手掌不可达、输入多 parent 和环路，累计 11 项测试通过且全部失败路径不创建输出。
- 阶段四：实现逐状态 20-transform `/tf`、独立非 latched 连接、原始记录流式保真摘要、唯一临时 bag、写后回读和原子替换；累计 14 项测试通过，覆盖默认/显式覆盖、同路径保护及注入验证失败清理。
- 真实输入：`test_output/01.bag`（SHA-256 `52975ffff4c1d3364e2d3323245f2ded3b6afee142bd4634d930a720b44748fa`）和 `urdf_kuavo5/urdf/biped_s300053_foxglove.urdf`（SHA-256 `ba49bc66b484da17bee2a3b48444ada914e9252bf9b396009a36c13dcb9532e5`），两者只读。
- 真实 dry-run：149,771 条输入消息、34.085906 秒、6,673 条状态（195.770 Hz）、单一稳定 12 通道名称布局、0 个异常状态、0 个裁剪和 0 个 header 时间回退；20 个目标 joint 完整，左右手掌可达，无目标 TF 冲突，合并图 95 条边且无环/无多 parent。
- 真实输出：保留 Git 忽略的 `test_output/issue-020/01_dexhand_tf.bag`，新增 6,673 条 `/tf` 和 133,460 个手指 transform，总消息数 156,444；脚本回读确认每条新增消息恰有 20 个唯一 child，parent/位姿/时间与 URDF 和状态反馈一致，原始消息及连接元数据保持不变。
- ROS1 交叉验证：`ros1_noetic` 容器通过宿主仓库到 `/workspace/VelaLoom` 的读写挂载运行；原生 `rosbag info` 成功读取输出，报告 656.4 MB、156,444 条消息、`/tf` 62,965 条（2 个连接）和 `/tf_static` 1 条。
- Foxglove：成功载入输出 bag；Transform Tree 显示从 `l_palm/r_palm` 到 20 个 prox/dist 手指 frame 的完整层级，3D 面板加载机器人模型。调用者已于 2026-08-29 确认左右手张合方向、部分闭合反馈、拖动和循环播放正确，身体、手臂和头部未受影响。
- 最终自动门禁：模块 14 项和全仓 53 项测试通过；全部脚本/测试 `py_compile`、CLI `--help`、交付代码无 TODO/FIXME、真实输出独立回读和 `git diff --check` 通过。可选 `ruff` 未安装在项目环境，因此未新增依赖或将其列为门禁。
- 依赖和产物：未新增依赖，继续使用 `VelaLoom` 的 `rosbags`；所有新夹具、缓存和真实输出均位于 `test_output/issue-020/`。已精确清理 `tmp/`、`pycache/` 和 `unit/`，仅保留 `01_dexhand_tf.bag`。自动化、ROS1 和 Foxglove 门禁全部通过，ISSUE-020 已标记为 `DONE`。

## ISSUE-006 测试记录（2026-08-29）

- 测试等级：L3。
- 变更范围：新增 `scripts/validate_tf.py`、`configs/validate_tf.yaml` 和 `tests/test_validate_tf.py`；同步 README、DECISIONS、TASK、PROGRESS 和 CHANGELOG；未修改转换脚本、URDF 或输入 bag。
- 阶段一至二：完成版本化配置、CLI 覆盖/来源、多值参数边界及全量 URDF 六类 joint 解析；11 项阶段测试覆盖缺省/显式配置、相对路径、非法 schema、link/joint 唯一性、axis/origin/limit、多 parent 和环路。
- 阶段三：实现 sensor、`/tf`、`/tf_static`、connection/caller 的只读扫描和联合图检查；累计 16 项测试覆盖单根、多 parent、环路、静态重复/冲突、多个 caller 的正常与冲突发布及非有限数据。
- 阶段四至五：实现 fixed/revolute/continuous/prismatic/planar/floating 几何、标量位置提取、单位分离限位、整组时间窗候选和源状态匹配；覆盖四元数正负等价、错误轴/平移、接反、索引交换、反号、度弧度、20 ms 延迟及超窗失败。
- 阶段六：实现缺失 joint 逐项/批量交互、非 TTY 策略、额外边/fixed dynamic/多自由度源策略、连续性、JSON、strict 和退出码；模块累计 25 项测试通过。
- 真实输入：`test_output/issue-020/01_dexhand_tf.bag`（656 MB，SHA-256 `c85308884b39e12d181bb528603303fd08656c623bf9a40d7faac7fa448b6913`）和 Foxglove URDF（SHA-256 `ba49bc66b484da17bee2a3b48444ada914e9252bf9b396009a36c13dcb9532e5`），前后哈希一致。
- 真实结果：`PASS_WITH_WARNINGS`；唯一根 `odom`，977,840 条动态 transform、45 条静态 transform，74/75 个 URDF joint 有 TF；28,146 组完整主体 TF 状态全部匹配，0 组超窗失败，角度 RMS `1.1838e-05 rad`、最大误差 `0.00190113 rad`（`zhead_2_joint`）。
- 已解释告警：`waist_camera` 采用 bag 的替代层级而缺少 URDF 直接边；21 条相机、雷达、`odom` 等 URDF 外扩展边；`zarm_r4_joint` 最大轻微限位超差 `0.00305414 rad`；TF/传感器采样差分得到的速度上限辅助指标。报告同时保留 49 个 TF joint 和 29 个 sensor joint 的独立连续性统计，位于 `test_output/issue-006/real-report.json`。
- 最终门禁：模块 25 项、全仓 78 项测试通过；全部脚本/测试 `py_compile`、CLI `--help`、配置/真实 JSON 审计、交付代码无 TODO/FIXME 和 `git diff --check` 通过。项目环境未安装可选 `ruff`、`pyflakes` 或 `mypy`，未新增依赖或将其冒充为已执行门禁。
- 依赖和边界：未新增依赖，使用 `VelaLoom` 已有 `rosbags` 与 PyYAML；未执行 ROS Noetic 命令，因为验证器直接只读 ROS1 bag 且不需要 ROS runtime。脚本未写出或修改任何 TF/bag/URDF。

## ISSUE-021 前期研究与一次性输出记录（2026-08-29）

- 研究边界：未编写或修改生产脚本；先只读分析 `test_output/issue-020/01_dexhand_tf.bag`，用户确认研究结论后另行明确授权生成一次性可视化派生 bag，全部产物位于 `test_output/issue-021/`。
- 输入基线：SHA-256 `c85308884b39e12d181bb528603303fd08656c623bf9a40d7faac7fa448b6913`，时长 34.081065 秒，`odom → base_link` 与完整主体 TF 共 28,146 组配对样本；原始水平范围约为 `x=0.191766 m`、`y=0.195831 m`。
- 频率结论：0.05–0.35 Hz 主要承载整体水平移动，约 1.0–1.1 Hz 存在明显步态成分；直接把原始坐标乘 3 会同步把低频趋势、步态残差和估计噪声放大。
- 对比结果：原始坐标直接 3 倍使水平可见范围、残差 RMS 和根加速度 P95 均约为原始 3 倍，支撑脚代理 0.25 秒漂移 P95 从 1.78 mm 增至 54.58 mm。平滑趋势 3 倍可保持根加速度基本不变，但仍会增加世界坐标中的脚滑。
- 当前推荐：100 Hz 等间隔分析、0.11 秒中值预处理、三阶零相位 Butterworth `0.25 Hz` 趋势、固定 `s=3`；只改变 `odom → base_link` 的水平 `x/y`，保留 `z`、旋转及所有后代相对 TF。该候选水平包围框对角线为原始 1.93 倍，根加速度 P95 为原始 0.998 倍，支撑脚代理漂移 P95 为 24.30 mm/0.25 秒。
- Foxglove 结论：双 3D 面板、`odom` Fixed/Display frame、俯视/斜侧视角、网格、慢放、循环和测量可在不改数据时使用；轨迹尾迹、位移箭头和初始残影需要 layout-local User Script/marker topic 或后续派生数据。零相位滤波需要整段数据，不适合在 User Script 中做无延迟实时等价实现。
- 风险与停止条件：3 倍根趋势无法保持真实支撑脚固定，只能作为明确标记的可视化增强；若 25 mm/0.25 秒脚滑门槛不可接受，应退回纯视觉增强。若要求接触物理一致性，则转入 ISSUE-022 的支撑约束和全身 IK，不在 ISSUE-021 扩张范围。
- 一次性输出：`test_output/issue-021/01_dexhand_tf_trend3x.bag`，656.4 MB，SHA-256 `3cbebb11095265158eaa2de259a95277f9244407e01733270abfda8b4ec4b0bf`。共 156,444 条消息，只重写 28,146 条 `odom → base_link` 的水平 `x/y`；非目标记录流、连接元数据、topic 数量、记录/header 时间、`z` 和旋转保持一致，公式最大误差为 0，残差保持误差 `8.33e-17 m`。
- 输出指标：水平包围框对角线由 0.274087 m 增至 0.528841 m（1.92946 倍），根加速度 P95 为原始 0.998382 倍，支撑脚代理 0.25 秒漂移 P95 为 24.3019 mm，通过 1.8 倍可见度、1.10 倍加速度和 25 mm 脚滑门槛。
- 验证：`rosbags` 写后回读与完整记录保真通过；通用 `validate_tf.py` 为 `PASS_WITH_WARNINGS`，唯一根 `odom`，28,146 组主体 TF 全部匹配源状态；`ros1_noetic` 容器原生 `rosbag info` 成功读取 156,444 条消息、62,965 条 `/tf` 和 1 条 `/tf_static`。输入 SHA-256 前后保持 `c85308884b39e12d181bb528603303fd08656c623bf9a40d7faac7fa448b6913`。
- 研究报告：`test_output/issue-021/research-report.md`；生成报告为 `bag-generation-report.json`，TF 报告为 `tf-validation.json`，另有完整指标和三张对比图。用户已确认该前期研究和一次性输出；可复用脚本当时尚未开发，后续仍需独立开发授权。

## ISSUE-021 可配置水平合成轨迹工具（2026-08-30）

- 测试等级：L3。新增独立模块 `scripts/loom_xy_motion.py` 和 `tests/test_loom_xy_motion.py`；同步 README、DECISIONS、TASK、PROGRESS 和 CHANGELOG；未修改其他转换脚本、URDF 或输入 bag。
- 阶段一：先冻结六个必传参数、三种时间格式、四个机器人相对方向、首帧 yaw 和 minimum-jerk 公式；6 项接口与数学测试通过。
- 阶段二至三：实现输入哈希、唯一目标连接、有限值、严格递增时间和单根拓扑只读扫描，以及换目录/重命名冲突循环、非 TTY 拒绝、EOF/取消、默认 `Y` 和零写入 dry-run；累计 15 项测试通过。
- 阶段四：实现原连接顺序流式复制、只重写目标 `x/y`、逐记录输入/输出联合回读、同目录唯一临时 bag、验证失败精确清理和基于硬链接的无覆盖原子发布；18 项模块测试覆盖目标字段保真、非目标原始字节、发布竞态及失败路径。
- 真实参数：基线 `test_output/issue-020/01_dexhand_tf.bag`，`robot-up`、`1.0 m`、`2.0–12.0 s`。首帧方向在 `odom` 中为 `(0.7391498542, -0.6735410106)`，起点 `(-0.0177784518, -0.0116624318)`，终点 `(0.7213714024, -0.6852034425)`，理论最大速度 `0.1875 m/s`。
- 真实输出：`test_output/issue-021/01_dexhand_tf_xy_motion.bag`，688,287,494 bytes，SHA-256 `8c8dabaa5c2d76ded9a22363b6e72bfc5fe72f4ade4878f8c3f91be4a08277a7`；156,444 条消息，只重写 28,146 个目标 transform，输入 SHA-256 保持 `c85308884b39e12d181bb528603303fd08656c623bf9a40d7faac7fa448b6913`。
- 交叉验证：`validate_tf.py` 输出 `test_output/issue-021/xy-motion-tf-validation.json`，状态 `PASS_WITH_WARNINGS`，唯一根 `odom`，28,146 组主体 TF 全部匹配源状态；ROS1 原生 `rosbag info` 成功读取 656.4 MB、156,444 条消息、62,965 条 `/tf` 和 1 条 `/tf_static`。
- Foxglove：新输出作为独立标签成功加载，沿用 `odom` Fixed/Display frame 和现有 URDF；3D 机器人、身体、双手及三路相机正常显示和播放。自动轨迹回读同时证明方向投影单调、横向误差不超过 `1e-9 m`、起终点保持且无回环。
- 最终门禁：模块 18 项、全仓 96 项测试通过；全部脚本/测试 `py_compile`、`loom_xy_motion.py --help` 和 `git diff --check` 通过。未新增依赖，使用现有 `rosbags 0.11.5`；仅保留真实输出与 TF JSON 报告，阶段临时目录和单元测试夹具在交付前精确清理。
- 边界：输出是可视化合成数据，覆盖原始根节点水平运动并可能产生脚底滑动；不用于定位、控制、训练或性能定量评估。批处理、里程计恢复、marker、接触重建和 IK 不在本 Issue 范围。
