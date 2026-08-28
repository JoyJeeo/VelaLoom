# 开发 Issue 清单

本文件是项目唯一的 AI 开发任务入口。每个 Issue 使用独立编号；一次开发只能执行一个 Issue。不同 Issue 不可以并行执行，项目同一时刻只允许一个 Issue 处于执行状态。

## 状态和串行规则

- `TODO`：待执行。
- `IN_PROGRESS`：正在执行；同一 Issue 同时只能有一个执行者。
- `BLOCKED`：被明确外部条件阻塞。
- `DONE`：验收完成。

开始开发前必须确认没有其他 `IN_PROGRESS` Issue；当前 Issue 完成或阻塞后，才能解锁并开始下一个 Issue。

## Issue 列表

### ISSUE-001：为 sync_frameid 增加真实 rosbag 端到端测试

- 状态：`DONE`
- 优先级：P1
- 目标：使用测试 bag 验证指定 topic 的 `header.frame_id` 被正确修改，输入 bag 保持不变。
- 范围：`scripts/sync_frameid.py`、测试文件或测试数据。
- 验收标准：
  - [x] 单个 bag 转换成功；
  - [x] 多个 topic 映射统计正确；
  - [x] 输入文件哈希未变化。
  - 验证：使用临时 ROS1 bag，`/cam/a` 和 `/cam/b` 各 2 条消息成功改写；未映射 `/other` 保持不变；输入 SHA-256 未变化。
  - 额外修正：递归输入时自动排除嵌套的输出目录，避免重复处理生成的 bag。

### ISSUE-002：补充 sync_frameid 批处理行为测试

- 状态：`DONE`
- 优先级：P1
- 目标：验证多个 bag、目录、`--recursive`、重名保护、`--dry-run` 和 `--overwrite`。
- 范围：测试文件和测试夹具。
- 验收标准：
  - [x] 多输入路径处理正确；
  - [x] dry-run 不写文件；
  - [x] 默认不覆盖，overwrite 显式覆盖。

### ISSUE-003：评估 TF frame 重写模式

- 状态：`DONE`
- 优先级：P2
- 目标：明确是否需要同时修改 `/tf` 和 `/tf_static` 中的父子 frame。
- 验收标准：
  - [x] 给出兼容性和风险结论；
  - [x] 必要时新增独立模式及测试（本次结论为暂不新增模式，增加拒绝改写的回归测试）。

### ISSUE-004：支持单个 --map 接收多个映射

- 状态：`DONE`
- 优先级：P1
- 目标：允许 `--map TOPIC=FRAME_ID ...` 在一次参数中接收多个映射，并在下一个 `--xxx` 选项处结束，减少批量相机映射命令的重复书写。
- 依赖：ISSUE-003
- 修改边界：`scripts/sync_frameid.py` 的命令行解析、对应参数测试和 README 使用示例；保留原有重复 `--map` 语法。
- 验收标准：
  - [x] 单次 `--map` 可接收多个合法映射；
  - [x] 遇到下一个长选项时停止收集，不吞并后续参数；
  - [x] 原有重复 `--map` 用法继续可用；
  - [x] 非法映射、空映射和冲突映射仍返回错误；
  - [x] 现有 rosbag 批处理、dry-run、overwrite 和 TF 安全边界测试全部通过；
  - [x] README、DECISIONS、PROGRESS 和 CHANGELOG 记录新接口及限制。

### ISSUE-005：统一 TF 发布权和根节点策略

- 状态：`DONE`
- 优先级：P0
- 目标：决定 `odom`、`base_link` 的根节点关系，明确原始 `/tf`、`/tf_static` 与 `robot_state_publisher` 的唯一发布权。
- 依赖：无
- 修改边界：仅设计和外部回放方案，不修改仓库代码。
- 验收标准：
  - [x] 明确唯一 TF 根和每类 TF 的唯一发布者；
  - [x] 明确播放时保留/过滤的 TF topic；
  - [x] 没有重复 child frame、TF 冲突或时间回退。

### ISSUE-006：开发通用 TF 数据验证脚本

- 状态：`DONE`
- 优先级：P0
- 目标：新增只读脚本 `scripts/validate_tf.py`，联合 ROS1 bag、必选 URDF 和传感器关节状态验证 TF 的拓扑、发布语义、URDF 运动学一致性、源数据映射、时间延迟与动作连续性；脚本不修改 bag、URDF，不生成、删除或重建任何 TF。
- 依赖：无；本 Issue 的验证器是通用工具，不把 29 个主体关节、当前 Kuavo URDF 或 ISSUE-006 的历史验收描述写死在程序逻辑中。
- 目标文件：
  - 实现：`scripts/validate_tf.py`；
  - 默认配置：`configs/validate_tf.yaml`；
  - 测试：`tests/test_validate_tf.py`；
  - 测试和真实数据报告：`test_output/issue-006/`；
  - 配套更新：`README.md`、`.ai/DECISIONS.md`、`.ai/PROGRESS.md` 和 `CHANGELOG.md`。

#### 修改边界

- 输入 bag 和 URDF 始终只读；脚本只输出终端报告和调用者显式指定的 JSON 报告，不写回输入，不产生转换 bag。
- 只新增 `validate_tf.py` 及其配置、测试和配套文档；不修改现有 TF 转换脚本、URDF、rosbag 或其他模块。
- 程序不得硬编码 joint 数量、Kuavo joint name、parent、child、axis、origin 或 limit；URDF 提供模型定义，YAML/CLI 只提供传感器数组索引到 URDF joint name 的跨数据源映射。
- 使用 `rosbags` 在 conda `VelaLoom` 环境完成只读分析；只有额外执行依赖 ROS Noetic runtime 的交叉验证时才使用已确认挂载的 `ros1_noetic` 容器。
- `callerid` 只作为 rosbag 连接诊断信息；允许多个 caller 发布不同 TF 边，不能把整个 `/tf` 只有一个 caller 当作通过条件。只有同一 child、同一边或同一发布权发生冲突时才失败。

#### 配置加载和参数优先级

- `--config PATH` 可选；显式提供时加载该文件，文件缺失、结构非法或版本不支持时立即失败。
- 未提供 `--config` 时，默认尝试读取仓库 `configs/validate_tf.yaml`；默认配置不存在时按“无配置”继续解析 CLI，因为 config 本身不是必传项。
- 参数实际值按 `CLI 参数 > 已加载配置 > 程序默认值` 解析；最终报告必须打印生效配置和每项来源，避免隐式覆盖。
- bag 和 URDF 是最终必需输入，可来自配置或 CLI；二者任一在合并后仍缺失时，必须在打开 bag、解析 URDF或创建报告前明确报错退出。
- `sensor-topic` 不是必传参数；未由 CLI 或配置指定时默认 `/sensors_data_raw`。指定 topic 在 bag 中不存在、必要字段缺失或消息结构不兼容时失败。
- CLI 相对路径相对当前工作目录解析；配置文件中的相对路径相对该配置文件所在目录解析；禁止把本机绝对路径写入默认配置。
- 当前默认配置允许使用相对路径声明指定分析基线 `../test_output/issue-017/01.bag` 和 `../urdf_kuavo5/urdf/biped_s300053_foxglove.urdf`，但自动化测试必须使用最小夹具并通过 CLI 或测试配置覆盖，不依赖真实大 bag 存在。

#### CLI 约定

- 建议接口：
  - `--config YAML`：覆盖默认配置文件路径；
  - `--bag BAG`：待验证的单个只读 ROS1 bag；
  - `--urdf URDF`：必选模型基准，可由配置提供；
  - `--sensor-topic TOPIC`：传感器源 topic，默认 `/sensors_data_raw`；
  - `--tf-topic TOPIC`：动态 TF topic，默认 `/tf`；
  - `--tf-static-topic TOPIC`：静态 TF topic，默认 `/tf_static`；
  - `--position-field PATH`、`--velocity-field PATH`、`--timestamp-field PATH`：传感器字段路径；
  - `--expected-root FRAME`：可选的唯一目标根；
  - `--joint-map INDEX=JOINT_NAME ...`：传感器数组索引映射；
  - `--missing-joint-policy fail|warn|ignore`、`--extra-edge-policy fail|warn|ignore`：非交互策略；
  - `--json-out JSON`：可选结构化报告；未指定时不创建文件；
  - `--strict`：把任意警告提升为失败。
- 所有可接收多个值的参数只允许写一次参数名，随后连续收集一个或多个值，遇到下一个 `--xxx` 选项结束；例如 `--joint-map 0=leg_l1_joint 1=leg_l2_joint`，不采用重复 `--joint-map`。
- CLI 提供列表参数时整体替换配置中的对应列表，不隐式追加或部分合并；重复出现同一个多值参数应明确报错。
- v1 的 `--bag`、`--urdf` 和各 topic 参数均为单值；未来若扩展为多值，也必须沿用“单个参数名后连续列出全部值”的语法。

#### 默认配置内容

- `configs/validate_tf.yaml` 使用版本化 schema，至少可声明 `inputs`、`topics`、`source`、`expected_root`、`matching`、`policies`、`thresholds` 和 `joints`。
- `source` 默认描述 `/sensors_data_raw.joint_data.joint_q`、可选 `joint_v` 和 `header.stamp`；程序不得使用 `sensor_time` 替代 header 时间，除非调用者显式配置。
- `joints` 完整记录当前已确认的 29 项 Kuavo 主体映射：索引 `0..5 → leg_l1_joint..leg_l6_joint`、`6..11 → leg_r1_joint..leg_r6_joint`、`12 → waist_yaw_joint`、`13..19 → zarm_l1_joint..zarm_l7_joint`、`20..26 → zarm_r1_joint..zarm_r7_joint`、`27 → zhead_1_joint`、`28 → zhead_2_joint`。
- YAML 不重复写入 URDF 已有的 joint type、parent、child、origin、axis、position/velocity limit；脚本按 joint name 从必选 URDF 读取这些模型属性。
- 默认时间匹配窗口为 TF 时间前 `30 ms`、后 `5 ms`：只在 `t_tf-30ms <= t_sensor <= t_tf+5ms` 内寻找同一条完整传感器消息；前窗覆盖正常“传感器先到、TF 后发布”，小后窗只容忍线程、记录顺序和时钟语义偏差。
- 默认策略为缺失 URDF joint 交互判断、额外 TF 边警告、fixed joint 出现在动态 `/tf` 警告；所有阈值必须在 `--help`、默认配置和 README 中说明含义及单位。

#### 输入预检和 URDF 解析

- 在扫描大 bag 前完成配置合并、必需输入、文件类型、路径、topic/字段、映射格式和输出路径检查；记录 bag 与 URDF 的输入 SHA-256，并在结束时确认未变化。
- 遍历 URDF 中全部 joint，不假定 29 个或任何固定数量；统计并验证 `fixed`、`revolute`、`continuous`、`prismatic`、`planar` 和 `floating` 类型。
- joint name 必须唯一，parent/child link 必须存在，origin 数值必须有限，适用类型的 axis 必须有限且非零，limit 必须合法；URDF 自身多 parent、环路或非法定义在接触 bag 数据前失败。
- parent-child 正反检查完全来自 URDF；YAML joint name 只把传感器数组索引关联到该 URDF joint，不参与定义或重复保存 parent-child。

#### TF 扫描、拓扑和发布语义

- 逐 transform 扫描 `/tf` 和 `/tf_static`，不能假设所有可动 joint 位于同一 `TFMessage`，也不能以消息内 transform 数量判断机器人自由度。
- 收集每条边的 parent、child、translation、rotation、header stamp、bag 记录时间、动态/静态来源、connection 和 callerid；检查所有数值有限且四元数规范化。
- 合并动态和静态边后检查根集合、可选 `expected_root`、环路、同一 child 多 parent、同一边位姿冲突、动态/静态发布权冲突和静态重复。
- URDF 中不存在的额外 TF 边必须完整列出；普通相机或传感器扩展边不得仅因“不在 URDF”自动失败，按配置、CLI 或交互策略处理。
- callerid 报告只说明 rosbag 观察到的 ROS 连接来源；`/nodelet_manager` 可能承载多个逻辑 nodelet，不能据此声称真实系统只有一个 TF 生成组件。

#### URDF 与 TF 几何验证

- 对全部 URDF joint 检查对应 parent→child 是否存在，不只检查有传感器映射的 29 项。
- `fixed`：TF 完整 translation/rotation 必须与 URDF origin 一致；出现在动态 `/tf`、动态/静态重复或位姿变化时按策略警告或失败。
- `revolute`/`continuous`：验证 `T_parent_child(t) = T_origin * R_axis(q(t))`；用 `inverse(R_origin) * R_tf(t)` 分离固定安装旋转，剩余旋转必须只绕 URDF axis，translation 必须保持 URDF origin；`revolute` 还需检查 position limit。
- `prismatic`：去除固定 origin 后只允许沿 URDF axis 平移，并检查位置 limit。
- `planar`/`floating`：按多自由度语义验证拓扑、有限数值和允许运动空间；不得错误套用单标量角度算法。实现能力不足时必须明确失败或由调用者确认降级，不能静默跳过。
- 角度比较必须处理四元数 `q/-q` 等价、角度环绕和非单位 axis；禁止通过裁剪、平滑或重写 TF 让验证通过。

#### 传感器映射和时间匹配

- 配置/CLI 映射表示 `position[index] → URDF joint name`；index 和 joint name 均不得重复，索引不得越界，joint 必须存在且为可动类型。
- 传感器消息的必要字段、数组长度、有限数值、header 时间和单调性必须验证；没有任何有效 joint 映射时无法证明 TF 来源，应明确失败而不是只做拓扑检查后声称完整通过。
- 允许映射只覆盖 URDF 可动 joint 的子集；未映射 joint 与 TF 中缺失 joint 分开报告，不自动从 URDF 顺序或 TF 顺序猜测数组映射。
- TF 频率低于传感器频率时不能按消息序号一一对应。对每个可比较 TF 状态，只在配置时间窗内选择同一条完整传感器消息，按全部已映射关节的整体误差和时间关系寻找候选；不得为不同 joint 分别选择不同时间的传感器值。
- 比较 `TF 提取关节位置 - sensor position[index]`，同时输出整体 RMS、最大单关节误差及 joint name、匹配时间差、无匹配数量、TF 跳过的传感器样本数和候选歧义。
- RMS 只表示一组已映射关节的总体误差，不能替代最大单关节门禁；找不到阈值内候选可能表示延迟超窗、滤波/插值、命令值来源、校准偏置、索引/符号/单位错误或 TF 本身错误，报告必须区分可证事实和推断。

#### 时间、连续性和限位

- 按每条动态边检查 header 时间回退、重复、最大间隔、更新频率、相邻位置变化、P50/P99/最大跳变和异常次数。
- 基于 `Δq/Δt` 的瞬时速度及其与 URDF velocity limit 的关系作为辅助指标；TF 跳过中间传感器样本会放大瞬时速度，不能只凭该指标硬失败，必须结合位置跳变、时间间隔和源状态匹配判断。
- 轻微 position limit 超差按配置容差警告，明显超差失败；只报告原始值，不裁剪、不滤波。
- 输入状态和 TF 均应分别检查连续性，避免把 TF 降采样造成的跳步误判为传感器本身不连续。

#### 缺失 joint 和交互决策

- 完整扫描后统一列出 URDF 中存在但联合 TF 中缺失的 joint，包括 name、type、parent 和 child；不得在扫描中途逐项提问。
- TTY 中每项支持 `Failure`、`Warning`、`Ignore`、`Abort`，并支持把选择应用到全部剩余缺失项；最终报告保留每项决定。
- 非 TTY 中发现缺失 joint 时必须使用已解析的 `missing-joint-policy`；未提供确定策略或策略仍为 `interactive` 时安全失败并列出全部候选。
- 对 URDF 可动 joint 没有传感器映射、额外 TF 边、fixed joint 位于动态 topic 等不同类别使用独立统计和策略，不把它们混成一个“缺失”结果。

#### 报告、状态和退出码

- 终端报告至少包含：输入路径/哈希、生效配置及来源、URDF joint 分类、TF 连接/callerid、根和拓扑、模型几何检查、传感器映射、时间匹配、RMS/最大误差、连续性、限位、缺失/额外项决策和最终状态。
- 状态为 `PASS`、`PASS_WITH_WARNINGS` 或 `FAIL`；`--strict` 将 `PASS_WITH_WARNINGS` 转为失败。
- 退出码：`0` 表示通过或带警告通过，`1` 表示数据验收失败，`2` 表示参数/配置/文件/消息结构错误，`3` 表示调用者中止交互。
- `--json-out` 未指定时不创建任何文件；指定时只写入已解析的目标，默认拒绝覆盖已有报告，并把所有测试报告写入 `test_output/issue-006/`。

#### 阶段门禁

- [x] 阶段一：冻结 CLI、默认配置 schema、参数优先级、多值参数边界、状态/退出码、交互策略和最小夹具；先完成参数与配置解析测试。
- [x] 阶段二：实现全量 URDF 解析、自身拓扑和 joint 类型合法性检查；立即覆盖 fixed/revolute/continuous/prismatic 以及非法模型单元测试。
- [x] 阶段三：实现 bag `/tf`、`/tf_static` 和 sensor topic 只读扫描、connection/callerid 记录及联合图检查；立即覆盖单根、多根、环、多 parent、重复和冲突。
- [x] 阶段四：实现 URDF origin/axis 与 fixed、revolute、continuous、prismatic TF 几何验证；立即覆盖 parent-child 接反、错误平移、错误轴、四元数等价和 limit。
- [x] 阶段五：实现数组映射、传感器字段读取、时间窗口候选、整体 RMS、最大单关节误差和降采样统计；立即覆盖索引交换、左右交换、反号、度/弧度、延迟、滤波和无匹配失败。
- [x] 阶段六：实现缺失 joint/额外边交互、非 TTY 策略、连续性/限位报告、JSON 和退出码；立即覆盖默认确认、批量决定、中止、strict 和无输出路径安全性。
- [x] 阶段七：在 `VelaLoom` 环境中对指定 `01.bag` 和 Foxglove URDF 做只读真实验证；确认输入哈希不变，预期结果为包含已解释告警的 `PASS_WITH_WARNINGS`，并人工复核完整报告。
- [x] 阶段八：运行模块测试、全仓回归、全部脚本/测试语法检查、`--help`、配置示例和 `git diff --check`；按 DOD 同步 README、DECISIONS、PROGRESS 和 CHANGELOG。

#### 自动化测试范围

- 配置缺省加载、显式 `--config`、纯 CLI、纯配置、CLI 覆盖配置、默认 `/sensors_data_raw`、缺少 bag/URDF 和非法 schema。
- 单个多值参数连续收集、遇下一个长选项停止、重复多值参数拒绝、CLI 列表整体替换配置列表。
- URDF joint 数量动态发现，fixed 和所有可动类型，非法 link/axis/origin/limit、URDF 环路和多 parent。
- TF 单根/多根/环路/多 parent、静态重复、位姿冲突、动态/静态冲突、多个 caller 的正常与冲突发布。
- fixed pose、revolute/continuous axis-angle、prismatic 位移、parent-child 接反、安装平移错误、错误轴、非规范四元数和 position limit。
- 传感器字段/长度/有限值/时间、映射重复或越界、joint 不存在、索引/左右交换、反号、度弧度错误、相邻和降采样匹配、延迟超窗及 RMS/最大误差门禁。
- 缺失 joint 的逐项和批量交互、额外边策略、非 TTY 安全失败、strict、JSON 覆盖保护、退出码和输入 SHA-256 不变。
- 所有生成夹具、缓存和报告仅位于 `test_output/issue-006/`，不依赖真实 644 MB bag 作为自动化门禁。

#### 验收标准

- [x] `scripts/validate_tf.py`、`configs/validate_tf.yaml` 和 `tests/test_validate_tf.py` 按上述边界交付，程序无机器人 joint 数量或几何硬编码。
- [x] 未传 `--config` 时默认读取 `configs/validate_tf.yaml`；CLI、配置和默认值优先级、实际生效配置及来源可验证。
- [x] 合并后缺少 bag 或 URDF 时在读取数据前失败；sensor topic 未配置时正确默认 `/sensors_data_raw`。
- [x] 所有多值参数使用单个参数名接收连续值，重复参数拒绝，CLI 列表整体替换配置列表。
- [x] URDF 全部 fixed 和 movable joint 均进入验证或明确的交互/策略结果；joint 数量和类型完全由 URDF 决定。
- [x] TF 拓扑、发布冲突、URDF parent-child/origin/axis/limit、传感器映射、时间延迟、RMS/最大误差和连续性均有自动化门禁。
- [x] 缺失 joint 可交互决定 Failure/Warning/Ignore/Abort，非 TTY 无确定策略时安全失败；额外边和其他语义异常独立报告。
- [x] 终端和可选 JSON 报告完整，状态、退出码、strict 和报告覆盖保护符合约定；未指定 JSON 时不创建文件。
- [x] 输入 bag 与 URDF SHA-256 不变；脚本不修改或生成 TF，不产生转换 bag。
- [x] 指定真实 `01.bag` 与 Foxglove URDF 的只读验证完成并保留可审计指标；模块测试、全仓回归、语法检查、`--help`、真实数据验证和 `git diff --check` 全部通过后才能标记 `DONE`。

### ISSUE-007：处理头部相机 frame 命名和层级

- 状态：`TODO`
- 优先级：P0
- 目标：统一 `camera_base`、`head_camera_base`、`head_camera_depth` 及头部图像 frame_id。
- 依赖：ISSUE-005
- 修改边界：外部 frame/header 适配或明确批准的模型设计。
- 验收标准：
  - [ ] 头部彩色和深度图像使用明确的 optical frame；
  - [ ] 每个头部图像 frame 都能从 `base_link` 查询；
  - [ ] 深度数据不再错误使用 color optical frame。

### ISSUE-008：统一雷达 frame 名称

- 状态：`TODO`
- 优先级：P1
- 目标：解决 `head_rader` 与 `head_radar` 的拼写不一致。
- 依赖：ISSUE-005
- 修改边界：外部 frame alias 或模型命名决策。
- 验收标准：
  - [ ] 只保留一个正式雷达 frame 名称；
  - [ ] `zhead_1_link → head_radar`（或最终选定名称）查询成功。

### ISSUE-009：处理腰部相机中间 frame 和外参

- 状态：`TODO`
- 优先级：P1
- 目标：解决 URDF 的 `waist_yaw_link → waist_camera` 与 bag 的 `waist_camera_base` 中间层差异。
- 依赖：ISSUE-005
- 修改边界：只在外部静态 TF 或批准的模型方案中处理，不覆盖原始 bag。
- 验收标准：
  - [ ] 明确 `waist_camera_base` 和 `waist_camera` 的物理含义；
  - [ ] 选定并记录唯一腰部相机外参；
  - [ ] `base_link → waist_camera` 查询成功。

### ISSUE-010：补齐左右手掌安装 TF

- 状态：`DONE`
- 优先级：P1
- 目标：补齐 `zarm_l7_link → l_palm` 和 `zarm_r7_link → r_palm`，使手指链挂到手臂末端。
- 依赖：ISSUE-006
- 修改边界：外部 TF/模型方案，先不修改仓库代码。
- 验收标准：
  - [x] 左右手掌 frame 从 `base_link` 可达；
  - [x] 手掌安装位姿来源可追溯。

### ISSUE-011：评估并处理 12→20 手指关节映射

- 状态：`DONE`
- 优先级：P1
- 目标：确定 `/dexhand/state` 的 12 个通道是否能通过耦合模型驱动 URDF 的 20 个手指关节。
- 依赖：无；本 Issue 只评估给定 bag 反馈与 URDF 手部链的可观测映射，不依赖主体 JointState 适配或新的手掌安装实现。
- 修改边界：不得把无法从 bag 观测到的 20 个真实角度假设为已知。
- 验收标准：
  - [x] 给出 12 个通道与 20 个 joint 的明确映射或证明不可唯一恢复；
  - [x] 近似方案记录比例、偏置、符号和误差；
  - [x] 完美方案列出需要补录的 20 个角度接口。
- 评估结论：每侧 6 个执行器反馈只能通过欠驱动耦合模型近似驱动 10 个 URDF 手指关节；四指的单通道不能唯一恢复 MCP/PIP 两个真实角度。本阶段采用用户确认的线性模型：反馈 `0` 为完全张开、`100` 为完全闭合，按 `u=clip(position/100, 0, 1)` 归一化；`thumb_aux→thumbCMC`、`thumb→thumbMCP`，其余每根手指同一个 `u` 同时驱动对应 MCP/PIP，并分别映射到各自 URDF `lower/upper`。左右镜像方向由 URDF `axis` 表达，不额外反号。
- 完美恢复边界：若要在接触物体时还原 20 个独立真实角度，需要新增逐关节编码器反馈或同步视觉关节角估计；当前 bag 的 12 个执行器状态不包含这些独立观测。
- 后续实现：由 ISSUE-020 的 `scripts/add_dexhand_tf.py` 将上述确认模型转换为 20 条动态指节 TF。

### ISSUE-012：补齐左右 D405 相机安装和传感器 TF

- 状态：`DONE`
- 优先级：P1
- 目标：补齐左右 `l_d405_camera_base`、`r_d405_camera_base` 及其 optical frame 到机器人主体的 TF。
- 依赖：ISSUE-006、ISSUE-010
- 修改边界：使用可追溯的安装标定外参，不凭空估计。
- 验收标准：
  - [x] `base_link → l_d405_camera_base` 查询成功；
  - [x] `base_link → r_d405_camera_base` 查询成功；
  - [x] 彩色/深度图像 frame 与对应 TF 一致。

### ISSUE-013：连接并规范 cam_h/cam_l/cam_r 相机树

- 状态：`DONE`
- 优先级：P1
- 目标：处理 bag 中 `cam_h_link`、`cam_l_link`、`cam_r_link` 三棵孤立相机树，确定它们与 URDF 相机 frame 的关系。
- 依赖：ISSUE-007、ISSUE-009、ISSUE-012
- 修改边界：外部静态 TF、frame/header 适配和回放配置。
- 验收标准：
  - [x] 不再存在孤立相机根节点；
  - [x] 每套相机的安装外参来源明确；
  - [x] 所有 image/CameraInfo frame 可从机器人根节点查询。

### ISSUE-014：建立静态 TF 去重和完整回放验收

- 状态：`TODO`
- 优先级：P1
- 目标：去除重复静态 TF 发布带来的歧义，并建立完整 rosbag→URDF→Foxglove 验收流程。
- 依赖：ISSUE-005 至 ISSUE-013
- 修改边界：独立输出目录和外部运行时适配；原始 bag 只读。
- 验收标准：
  - [ ] 原始 bag SHA-256 不变；
  - [ ] 每条静态 parent→child 只由一个来源发布；
  - [ ] TF 只有一个主根；
  - [ ] 身体、手部、相机和雷达数据均能正确显示；
  - [ ] 无 TF 冲突、重复数据、时间回退和连接异常。

### ISSUE-015：实现统一 TF 输出 bag 转换脚本

- 状态：`DONE`
- 优先级：P0
- 目标：新增一个独立脚本，将原始 ROS1 bag 复制为统一 TF 输出 bag；保持原始 `/tf` 动态数据不变，重建去重后的 `/tf_static`，补齐 URDF 相机安装固定边，并按已确认的单位变换把 `cam_h_link`、`cam_l_link`、`cam_r_link` 接入机器人树，使输出 bag 的相机 frame 能从唯一根 `odom` 查询。
- 依赖：ISSUE-005（唯一根和发布权契约）；桥接关系采用本 Issue 中已由用户确认的方案，不等待外部标定。
- 与 ISSUE-013/014 的关系：本 Issue 交付可复现的输出 bag 转换工具；ISSUE-013 继续负责相机 frame 语义和数据 header 的专项决策，ISSUE-014 负责完整身体/手部/相机/雷达回放验收。
- 目标脚本：`scripts/unify_rosbag_tf.py`。
- 输入输出：输入单个 ROS1 bag 和现有 Foxglove URDF；输出新 bag 到独立路径，默认拒绝覆盖，显式 `--overwrite` 才允许覆盖；原始 bag 始终只读。
- 建议接口：
  - `--input BAG`：输入 bag；
  - `--output BAG`：输出 bag；
  - `--urdf URDF`：用于读取 fixed joint，默认指向 `urdf_kuavo5/urdf/biped_s300053_foxglove.urdf`；
  - `--overwrite`：允许覆盖已有输出；
  - `--dry-run`：只分析、输出统计和 TF 检查，不写 bag；
  - `--keep-legacy-head-chain`：保留 `zhead_2_link → head_camera_base → head_camera_depth` 旧链，默认关闭；若检测到消息仍引用旧 frame，默认应报错并要求显式选择。
- `/tf` 行为：原样复制所有动态消息、时间戳、连接元数据和变换内容，尤其保留 `odom → base_link`；不得把动态 TF 改写为静态 TF。
- `/tf_static` 行为：收集原始静态变换，按 `parent, child` 去重；相同位姿重复边合并，位姿冲突或同一 child 多 parent 时失败；输出一份规范化的 latched `/tf_static`。
- 必须补充的 URDF 相机固定边：
  - `zhead_2_link → camera_base`；
  - `zarm_l7_link → l_camera_link`；
  - `l_camera_link → l_d405_camera_base`；
  - `l_d405_camera_base → l_d405_camera`；
  - `zarm_r7_link → r_camera_link_connect`；
  - `r_camera_link_connect → r_d405_camera_base`；
  - `r_d405_camera_base → r_d405_camera`。
  位姿从 URDF fixed joint 读取，不在代码中重复硬编码；已存在且一致的边不得重复写入。
- 必须增加的相机桥接边，全部使用单位变换 `translation=(0,0,0), quaternion=(0,0,0,1)`：
  - `camera_base → cam_h_link`；
  - `l_d405_camera_base → cam_l_link`；
  - `r_d405_camera_base → cam_r_link`。
- 头部旧链策略：默认输出采用 `zhead_2_link → camera_base → cam_h_link`；若全 bag 扫描发现普通消息 header 使用 `head_camera_base` 或 `head_camera_depth`，不得静默删除旧链，应失败并列出引用 topic，或由 `--keep-legacy-head-chain` 显式保留。
- 相机内部 TF：保留 bag 已有的 `cam_*_link → color/depth frame → optical frame`，不把图像 header 改成 URDF 机械 link；头部深度使用 color optical frame 的问题不在本 Issue 内改变。
- 输出 TF 树验收：合并 `/tf` 和规范化 `/tf_static` 后根集合为 `{odom}`；`base_link` 唯一 parent 为 `odom`；`cam_h/l/r_link` 不得为根；每个 child 只有一个 parent；左/右/头部 optical frame 均存在从 `odom` 的路径。
- 数据安全验收：输入 bag SHA-256 不变；dry-run 不创建输出；默认不覆盖；失败时清理临时输出；输出消息数量、非 TF topic 和原始动态 TF 不被意外改变。
- 阶段门禁：
  - [x] 阶段一：确定 CLI、URDF fixed joint 读取范围、桥接清单和旧头部链检测规则；只读扫描真实 bag 并记录基线；
  - [x] 阶段二：先实现 `/tf_static` 收集、去重、冲突检测和三组单位桥接的最小转换；立即使用最小 ROS1 bag 夹具测试；
  - [x] 阶段三：加入 URDF fixed joint 补齐、旧头部链安全过滤和输出原子写入；立即进行输出可读性、输入 SHA-256 和失败清理测试；
  - [x] 阶段四：使用真实 bag 生成独立输出，运行 rosbags 可读性和 TF 树检查；`ros1_noetic` 容器因宿主挂载 `/Volumes/yuto2` 不存在而无法启动，已记录为环境限制；
  - [x] 阶段五：完整测试、语法检查、`git diff --check`、README/DECISIONS/PROGRESS/CHANGELOG 同步。
- 当前限制：本 Issue 只解决统一根、相机安装链和相机驱动 frame 接入；20 个手指动态 TF、雷达命名、腰部相机命名和头部深度 header 仍由后续 Issue 处理。

### ISSUE-016：将 URDF 全部 fixed joint 交互式写入 rosbag

- 状态：`DONE`
- 优先级：P0
- 目标：新增一个独立脚本，读取传入 URDF 中全部 `type="fixed"` joint，将每条 `parent link → child link` 转换为静态 TF；结合传入 ROS1 bag 中已有的 `/tf_static` 和 `/tf` 进行冲突分析，由调用者交互式决定每个冲突的处理方式，随后将最终唯一静态边集合记录到新的输出 bag。
- 依赖：无；本 Issue 不依赖 ISSUE-015，也不导入、调用或修改 `unify_rosbag_tf.py`、`sync_frameid.py` 等已有脚本。
- 目标脚本：`scripts/add_urdf_tf_static.py`。
- 修改边界：
  - 只新增独立脚本、对应独立测试和该脚本的用户文档；
  - 不修改输入 URDF，不原地修改输入 bag；
  - 不加入 ISSUE-015 的 `camera_base → cam_h_link`、`l_d405_camera_base → cam_l_link`、`r_d405_camera_base → cam_r_link` 三条单位桥接；
  - 不处理 `revolute`、`continuous`、`prismatic`、`floating`、`planar` 等可动 joint；
  - 不自动重命名、合并或猜测语义相似的 frame。
- 建议接口：
  - `--input BAG`：只读输入 ROS1 bag；
  - `--output BAG`：独立输出 bag，必须与输入路径不同；
  - `--urdf URDF`：待读取全部 fixed joint 的 URDF；
  - `--dry-run`：只扫描并输出分类、冲突和候选决策，不交互、不写文件；
  - `--overwrite`：显式允许替换已存在的输出；默认拒绝覆盖；
  - `--decisions-in JSON`：加载先前已确认的冲突决策；决策与当前输入不匹配时失败；
  - `--decisions-out JSON`：保存本次交互决策和输入摘要，便于审计与重放；
  - `--yes`：用于已提供完整 `--decisions-in` 的非交互运行；不得替代尚未解决的冲突选择。
- URDF fixed joint 读取规则：
  - 遍历所有直接 joint，选择 `type="fixed"`，不使用 parent/child 白名单；
  - TF parent/child 分别来自 joint 的 `<parent link="...">` 与 `<child link="...">`；
  - translation 来自 `<origin xyz>`，rotation 由 `<origin rpy>` 转换为规范化四元数；无 `<origin>` 时使用单位变换；
  - frame 名称必须非空；xyz/rpy 必须各为 3 个有限数值；URDF 内同一 child 多 parent、重复边位姿不一致或非法四元数必须在接触输出前失败；
  - `link` 元素本身不单独生成 TF；只有 fixed joint 定义 parent link 到 child link 的固定关系。
- bag 只读扫描和分类：
  - 收集原始 `/tf_static` 的所有 `parent, child, translation, rotation`，按边和位姿去重；
  - 收集原始 `/tf` 的动态 parent-child 关系、出现次数和所属消息；
  - 将每条 URDF fixed 边分类为“bag 中缺失”“完全一致已存在”或“需要调用者判断的冲突”；
  - 完全一致边不是冲突，输出中只保留一次并记录为 `already_identical`；
  - 冲突包括：同一 parent-child 位姿不同、同一 child parent 不同、fixed child 同时出现在动态 `/tf`、bag 自身同一 child 多 parent；
  - 可疑相似名称只输出警告，例如当前数据中的 `head_rader` 与 `head_radar`，不得自动重命名或视为同一 frame。
- 交互式冲突处理：
  - 所有扫描和冲突发现必须在创建临时输出前完成；
  - 每个冲突必须显示 child、URDF parent/位姿、bag parent/位姿、动态出现次数和具体影响；
  - 静态冲突提供：`使用 URDF`、`保留 bag`、`中止转换`；不得设置默认选项，空输入或非法输入必须重新询问；
  - 动态 `/tf` 冲突提供：`保留动态 TF 并跳过 URDF fixed`、`删除对应动态变换并使用 URDF fixed`、`中止转换`；选择删除动态变换时必须再次输入完整 `YES` 确认，并报告将修改的消息/transform 数量；
  - 选择保留 bag 时必须在最终摘要中明确标记该 URDF fixed joint 未写入，显示最终 URDF fixed 覆盖率；
  - 调用者中止、stdin EOF、非 TTY 且没有完整决策文件时，安全失败且不创建输出；
  - 同一个结构冲突只询问一次，后续重复消息复用该次选择。
- 最终确认规则：
  - 所有冲突解决后，先显示输入消息数量、URDF fixed 数量、完全一致数量、新增数量、各类冲突选择、被删除动态 transform 数量、最终 fixed 覆盖率和输出路径；
  - 使用提示 `Proceed with writing OUTPUT.bag? [Y/n]`；默认值为 `Y`，用户直接按 Enter、输入 `y` 或 `yes` 时开始写出，输入 `n` 或 `no` 时中止且不创建输出；
  - 默认 `Y` 只适用于最终写出确认，不适用于任何冲突处理选择；
  - 非交互运行必须显式使用 `--yes`，并且所有冲突已由 `--decisions-in` 完整解决，否则失败。
- 决策记录：
  - `--decisions-out` 保存每个冲突的类型、URDF 候选、bag 候选、选择结果、动态删除数量、输入 bag SHA-256 和 URDF SHA-256；
  - `--decisions-in` 重放前必须核对两个输入 SHA-256 和冲突候选集合，防止把旧决定应用到新数据；
  - 决策文件只记录选择，不把判断逻辑隐藏在配置中；实际运行仍输出将采用的全部决定。
- 输出行为：
  - 最终静态集合由调用者保留的 bag 静态边、全部无冲突 URDF fixed 边以及调用者选择使用的 URDF 冲突边组成；
  - 每个唯一 parent-child 只在一条规范化、latched 的 `/tf_static` 消息中出现一次；
  - bag 记录时间使用原 bag 最早 `/tf_static` 时间；输入没有静态消息时使用原 bag 起始时间，避免时间零扩大 bag 时间范围；URDF 新边的 Header stamp 使用零时间；
  - 未经调用者明确选择删除的 `/tf` 变换必须保持原始序列化内容、时间戳和连接元数据；
  - 非 TF topic 必须保持原始序列化内容、时间戳、消息数量和连接元数据；
  - 输出先写入同目录临时文件，全部写出和回读验证通过后原子替换目标；失败或中止必须清理临时输出；
  - 输出默认不覆盖，只有 `--overwrite` 才允许覆盖现有输出。
- 当前真实数据只读基线：
  - Foxglove URDF 含 26 条 fixed joint；
  - 其中 15 条与原始 bag 的 parent-child 已一致，10 条为 bag 中缺失的新边；
  - 存在 1 条结构冲突：URDF 为 `waist_yaw_link → waist_camera`，bag 为 `waist_camera_base → waist_camera`，必须在转换时由调用者选择；
  - 存在名称相似警告：URDF `head_rader` 与 bag `head_radar`，只报告，不自动合并。
- 阶段门禁：
  - [x] 阶段一：冻结独立 CLI、全量 fixed joint 解析规则、冲突分类、交互文案、默认 `[Y/n]` 和决策 JSON schema；使用真实 URDF/bag 只读复核 26/15/10/1 基线；
  - [x] 阶段二：先实现全量 URDF fixed 解析和纯内存冲突分析；用最小 URDF/bag 夹具验证缺失、一致、位姿冲突、多 parent 和动态冲突；
  - [x] 阶段三：实现交互选择、二次危险确认、默认 `Y` 最终确认、EOF/非 TTY 安全失败及 decisions 文件保存/重放；通过模拟 stdin 自动化测试；
  - [x] 阶段四：实现规范化 latched `/tf_static`、按决策改写动态 TF、原子输出和回读验证；验证输入 SHA-256、消息/连接不变量和失败清理；
  - [x] 阶段五：使用指定真实 URDF/bag 先 dry-run，再由调用者完成腰部冲突交互选择后生成独立输出，检查最终 TF 树、fixed 覆盖率和可读性；
  - [x] 阶段六：完整测试、语法检查、`git diff --check`，同步 README、DECISIONS、PROGRESS 和 CHANGELOG。
- 验收标准：
  - [x] 新脚本与既有脚本无导入、调用或相机桥接依赖；
  - [x] 能从传入 URDF 读取并报告全部合法 fixed joint，当前 URDF 为 26 条；
  - [x] 无冲突边和完全一致边不需要人工判断，最终各只出现一次；
  - [x] 每个真实冲突都由调用者交互或已校验 decisions 文件明确选择，脚本不自动选边；
  - [x] 最终写出提示为 `[Y/n]` 且默认 `Y`，冲突选择没有默认值；
  - [x] 调用者中止、EOF、决策不完整或输入哈希不匹配时不创建输出；
  - [x] 输出只有一条 latched `/tf_static`，每个 child 只有一个最终 parent；
  - [x] 未经选择修改的动态 `/tf`、全部非 TF topic 和输入文件保持不变；
  - [x] decisions 文件可审计、可重放且不会跨输入误用；
  - [x] 自动化测试、真实数据验证、语法检查和 `git diff --check` 全部通过，无未解释跳过。

### ISSUE-017：将 unify_rosbag_tf 修复为交互式 TF 森林整理工具

- 状态：`DONE`
- 优先级：P0
- 目标：修改 ISSUE-015 已交付的 `scripts/unify_rosbag_tf.py`，使其仅读取传入的 ROS1 bag，联合分析 `/tf` 和 `/tf_static`，去除重复静态 TF，完整打印 TF 森林，由调用者交互式选择目标根以及每棵剩余树应挂载到的 link，再使用单位变换连接各树，生成单根、无环、无多 parent 的新 bag。
- 依赖：ISSUE-015（已完成）；本 Issue 是对其已交付脚本的独立需求变更，不修改 ISSUE-015 的历史方案、状态或验收记录。
- 修改边界：`scripts/unify_rosbag_tf.py`、`tests/test_unify_rosbag_tf.py` 及该脚本的 `README.md`、`.ai/DECISIONS.md`、`.ai/PROGRESS.md`、`CHANGELOG.md` 配套记录；不修改其他转换脚本，不修改输入 bag，不修改 URDF。

#### CLI 和旧专用规则删除

- 保留 `--input BAG`、`--output BAG`、`--overwrite`、`--dry-run`；输入与输出必须为不同路径，默认拒绝覆盖。
- 删除 `--urdf`、URDF XML 解析、默认 URDF 路径和七条相机安装 fixed joint；继续传入 `--urdf` 时必须由命令行解析器报未知参数。
- 删除写死的 `camera_base → cam_h_link`、`l_d405_camera_base → cam_l_link`、`r_d405_camera_base → cam_r_link` 桥接；它们由通用交互挂载取代。
- 删除旧 head camera 链自动删除、普通消息 header 引用检查和 `--keep-legacy-head-chain`；bag 中已有 head camera TF 原样保留。

#### TF 扫描、去重与图验证

- `/tf_static`：收集全部静态 transform；相同 parent、child 且位姿相同的边只保留一条；同边位姿冲突、空 frame 或同一 child 多 parent 时在接触输出前失败。
- `/tf`：仅提取唯一 parent→child 边用于拓扑分析；不把不同时间的动态样本当作重复数据，不删除、修改或重新序列化原始动态消息。
- 联合 `/tf` 与去重后 `/tf_static` 构图；交互前检查空 frame、child 多 parent、环路、无根连通分量和空 TF 数据，任一检查失败时返回非零状态且不创建输出。
- 最终把去重后原始静态边和交互新增边重建为一条 latched `/tf_static` 消息。

#### 完整 TF 森林终端日志

- 调用者回答任何交互问题前，必须完整打印修复前 TF 森林，不截断 frame。
- 每棵树显示序号、根 frame、frame 数量和完整层级；边标记 `[D]` 动态 `/tf`、`[S]` 静态 `/tf_static`、`[B]` 同时存在于两个 topic。
- 标记每棵树是否包含 `map`、`odom`、`base_link`；按 `map > odom > base_link` 给出推荐目标树，但不自动代替调用者选择。
- 树、根和同级 frame 按名称稳定排序，保证日志可复现和可测试。
- 所有挂载完成后，在最终写入确认前再完整打印修复后 TF 树、去重摘要、新增边和拓扑验证结果。
- 日志还必须包含输入/输出路径、TF 消息数、动态唯一边数、静态输入/重复/保留数、用户每项选择、最终根和写入/dry-run/取消/失败结果。

#### 交互式根选择和逐树挂载

- 只有一棵 TF 树时不询问目标根、不新增连接边，继续去重、验证和写入确认。
- 有多棵树时列出所有真实根；调用者可按序号或根 frame 名选择目标树，脚本不删除、反转或重排目标树内部已有边。
- 对每棵剩余树逐一打印完整子树，调用者必须输入该根应挂载到“当前已合并目标树”中的哪个 parent link；不得默认把其他根直接挂到目标根下。
- 挂载 parent 必须存在于当前已合并目标树，不能位于待挂载树，不得造成环路或多 parent；无效输入必须打印具体原因并重新询问。
- 每次挂载新增 `selected_parent_link → detached_root` 静态边，使用已确认的单位变换 `translation=(0,0,0)`、`quaternion=(0,0,0,1)`；挂载完的树立即纳入当前目标树，后续根可挂载到其 frame 上。
- 交互命令至少支持 `list`（打印可用 parent link）、`tree`（重新打印当前完整 TF 森林）和 `abort`（取消且不创建输出）。
- 多根且 stdin 不是 TTY 时安全失败，列出所有根并说明需要交互选择；本 Issue 不新增自动决策参数或决策文件。

#### 写入确认、dry-run 和数据安全

- 完成全部挂载后，必须先验证只有一个根、所有 frame 连通、无环且每个 child 只有一个 parent，再进入写入确认。
- 正式写入提示为 `Proceed [Y/n]:`；直接回车、`y` 或 `yes` 继续，`n` 或 `no` 取消，其他输入重新询问，EOF 安全取消。
- `--dry-run` 仍执行完整扫描、树打印、根选择、逐树挂载和最终拓扑验证，但不询问是否写入，不创建临时或最终 bag。
- 输入 bag 始终只读；非 TF 消息和动态 `/tf` 必须按原始字节、时间戳、顺序和连接元数据复制。
- 所有分析、交互和确认完成后才创建同目录临时 bag；写完后重新打开验证数据与 TF 不变式，通过后原子替换最终输出；失败或取消只清理本次精确临时路径。

#### 阶段门禁

- [x] 阶段一：固化新 CLI、图数据结构、完整树日志格式、交互语法和最小夹具；先写参数、扫描、去重、拓扑和日志回归测试。
- [x] 阶段二：实现 bag-only 扫描、静态去重、连通分量/根/环路/多 parent 分析和完整森林打印；立即运行对应单元测试。
- [x] 阶段三：实现目标根与逐树挂载 link 交互、单位边计划、`list/tree/abort` 和无效选择重试；立即运行交互回归测试。
- [x] 阶段四：实现 `[Y/n]` 默认写入、dry-run、非 TTY 安全失败、原子写出和写后复核；立即运行输入哈希、原始消息保真、覆盖保护和失败清理测试。
- [x] 阶段五：在 `VelaLoom` 中使用真实 bag 验证修复前四棵树、交互挂载三棵相机树和修复后单根；如使用 ROS Noetic 命令复核，先检查 `ros1_noetic` 挂载，产物仅写入宿主 `test_output/issue-017/`。
- [x] 阶段六：运行完整回归、语法检查、`git diff --check` 和 DOD；同步 README、DECISIONS、PROGRESS 和 CHANGELOG。

#### 验收标准

- [x] 脚本仅需 bag 输入和输出，`--urdf` 与 `--keep-legacy-head-chain` 已删除并作为未知参数失败。
- [x] 交互前完整打印 TF 森林，包含边来源、全部根、frame 数和 `map/odom/base_link` 推荐。
- [x] 多根时调用者能选择目标根，并为每个其他根分别选择当前目标树中的挂载 link；脚本不自动把其他根直接挂到目标根。
- [x] 所有新增连接都为调用者确认的 `selected_parent_link → detached_root` 单位静态变换，不存在写死相机 frame 规则。
- [x] 静态重复边去重；位姿冲突、多 parent、环路、无根分量和非法挂载安全失败，不创建部分输出。
- [x] 修复后只有一个根，所有 frame 可达，无环且每个 child 只有一个 parent；修复后完整树和验证结果在写入前打印。
- [x] `Proceed [Y/n]:` 直接回车默认写入，否定、EOF 或中止不创建输出；`--dry-run` 完成交互和验证但不写文件。
- [x] 输入 bag SHA-256 不变；原始动态 `/tf` 和非 TF 消息的字节、时间戳、顺序和连接元数据保持不变；最终静态边仅写为一条 latched `/tf_static`。
- [x] 默认覆盖保护、`--overwrite`、临时文件精确清理和写后重新打开验证均通过。
- [x] 自动化测试覆盖单树、多树、根推荐、逐树挂载、重复、冲突、环路、非 TTY、dry-run、默认确认和失败清理；所有生成物仅位于 `test_output/issue-017/`。
- [x] README、DECISIONS、PROGRESS 和 CHANGELOG 已同步；完整测试、真实 bag 验证、`git diff --check` 和 DOD 通过后才能标记 `DONE`。

### ISSUE-018：将 TF 来源标记移到名称末尾

- 状态：`DONE`
- 优先级：P2
- 目标：调整 `unify_rosbag_tf.py` 的终端树形日志，把 `[D]`、`[S]`、`[B]` 从 frame 名称前移到名称末尾，并统一新增桥接边摘要的标记位置。
- 依赖：ISSUE-017（已完成）。
- 修改边界：`scripts/unify_rosbag_tf.py`、`tests/test_unify_rosbag_tf.py` 及配套任务、进展和变更记录；不改变 TF 分析、交互选择、转换结果或输入输出数据。
- 验收标准：
  - [x] 完整树和子树均显示为 `frame [D/S/B]`；
  - [x] 新增边摘要显示为 `parent → child [S]`；
  - [x] 同级 frame 排序和所有既有功能保持不变；
  - [x] 模块测试、完整回归、语法检查和 `git diff --check` 通过。

### ISSUE-019：为 TF 树日志增加层级连接线

- 状态：`DONE`
- 优先级：P2
- 目标：将 `unify_rosbag_tf.py` 的纯空格缩进改为带 `├──`、`└──`、`│` 的树形层级显示，明确同级分支、末尾分支和父子延续关系。
- 依赖：ISSUE-018（已完成）。
- 修改边界：`scripts/unify_rosbag_tf.py`、`tests/test_unify_rosbag_tf.py` 及配套文档和交付记录；不改变 TF 拓扑、排序、交互或 bag 输出。
- 验收标准：
  - [x] 所有完整树和子树使用标准树形连接线；
  - [x] 中间同级节点使用 `├──`，最后节点使用 `└──`，仍有后续同级分支的祖先层使用 `│`；
  - [x] frame 名称末尾保留 `[D]/[S]/[B]`，同级名称排序不变；
  - [x] 模块测试、完整回归、语法检查和 `git diff --check` 通过。

### ISSUE-020：根据灵巧手反馈补充 20 条动态指节 TF

- 状态：`DONE`
- 优先级：P0
- 目标：新增独立脚本 `scripts/add_dexhand_tf.py`，从 ROS1 bag 的 `/dexhand/state.position` 读取左右手实际反馈，按 ISSUE-011 已确认的 12→20 线性耦合模型和传入 URDF 的运动学定义计算左右手 20 条动态指节 TF，并写入新的输出 bag，使 Foxglove 在 `Transforms` 模式下显示手指实际反馈姿态。
- 依赖：ISSUE-011（映射和近似边界已确认）、ISSUE-016（固定关节补全工具已完成）；新脚本不得导入或调用 `add_urdf_tf_static.py`，只要求输入 TF 树中左右手掌可从机器人主体到达。
- 目标文件：
  - 实现：`scripts/add_dexhand_tf.py`；
  - 测试：`tests/test_add_dexhand_tf.py`；
  - 测试和真实数据产物：`test_output/issue-020/`；
  - 配套更新：`README.md`、`.ai/DECISIONS.md`、`.ai/PROGRESS.md` 和 `CHANGELOG.md`。

#### 修改边界

- 输入 bag 和 URDF 始终只读；输出必须为另一个 `.bag` 文件，不修改或覆盖输入。
- 仅新增由 `/dexhand/state` 推导的手部动态 `/tf`；原有 `/tf`、`/tf_static`、身体、腿、腰、手臂、头部、相机及全部非 TF 消息保持不变。
- 不修改 URDF，不修改 `add_urdf_tf_static.py`、`unify_rosbag_tf.py` 或其他转换工具。
- 不读取 `/control_robot_hand_position` 作为姿态来源；命令值只能用于诊断，输出姿态必须以 `/dexhand/state.position` 的反馈观测为准。
- 不生成全机器人 `JointState`，不切换 Foxglove 到 `Joint states` 模式。
- 不做反馈滤波、插值、降采样或按 bag 内峰值重新标定；反馈未达到 `100` 时必须保留部分闭合姿态，不能把观测最大值重映射成完全闭合。
- 当前单通道驱动 MCP/PIP 的结果是已确认的可视化近似，不声称恢复了接触状态下的两个独立真实角度。

#### 已确认的反馈和关节映射

- 必须按 `sensor_msgs/JointState.name` 查找对应 `position`，不得依赖数组下标或固定排列顺序。
- 每侧反馈通道：`thumb`、`thumb_aux`、`index`、`middle`、`ring`、`pinky`；左右手分别使用 `l_`、`r_` 前缀。
- 每侧映射：
  - `thumb_aux → thumbCMC`；
  - `thumb → thumbMCP`；
  - `index → indexMCP + indexPIP`；
  - `middle → middleMCP + middlePIP`；
  - `ring → ringMCP + ringPIP`；
  - `pinky → littleMCP + littlePIP`。
- 反馈约定：`0` 为完全张开，`100` 为完全闭合；归一化使用 `u = clip(position / 100, 0, 1)`。
- 关节角统一从 URDF limit 计算：`q = lower + u * (upper - lower)`；当前 URDF 对应 `thumbCMC=1.5708u`、`thumbMCP≈0.8727u`、四指 `MCP=1.309u`、`PIP=1.25u`。
- 左右镜像方向由 URDF 的 joint `axis` 决定；映射层不对右手角度额外反号。
- 超出 `[0,100]` 的有限反馈允许裁剪，但必须统计 topic、通道、方向和数量并输出警告；NaN、Inf、缺失名称、重复名称或 `name/position` 长度不一致必须在写出前失败。

#### URDF 运动学和动态 TF 生成

- 从传入 URDF 读取 20 个目标 `type="revolute"` joint 的 `parent`、`child`、`origin xyz/rpy`、`axis` 和 `limit lower/upper`，不得在代码中重复硬编码平移、零位旋转、转轴或角度上限。
- 每个关节的父子变换使用 `T_parent_child(q) = T_origin * R_axis(q)`；`T_origin` 来自 URDF `xyz/rpy`，`R_axis(q)` 为绕归一化 URDF axis 的轴角旋转，最终四元数按 ROS `x,y,z,w` 顺序输出并规范化。
- 每条有效 `/dexhand/state` 生成一条新的 `/tf` `TFMessage`，其中恰好包含左右手共 20 个 `TransformStamped`；“20 条动态 TF”表示 20 种唯一 parent→child 边，它们随每条反馈消息重复发布。
- 每个 `TransformStamped.header.stamp` 使用来源 `/dexhand/state.header.stamp`；新增 `/tf` 的 bag 记录时间使用来源状态消息的记录时间。header 时间为零时回退到 bag 记录时间并报告回退数量。
- 目标 child frame 在输入 `/tf` 或 `/tf_static` 中已存在、parent 不一致、输入 TF 已多 parent 或新增后形成环路时默认安全失败；不得自动覆盖、删除或抢占已有 TF 发布权。
- 写出前必须确认 `l_palm`、`r_palm` 已存在于输入联合 TF 图，并且均可从 `base_link` 到达；脚本只补充手掌以下指节动态边。

#### CLI 和输出行为

- 建议接口：
  - `--input BAG`：只读输入 ROS1 bag；
  - `--output BAG`：独立输出 bag，必须与输入不同；
  - `--urdf URDF`：包含目标手部关节的 URDF；
  - `--state-topic TOPIC`：反馈 topic，默认 `/dexhand/state`；
  - `--dry-run`：完整扫描、映射、范围统计、URDF/TF 验证和输出数量预测，不创建文件；
  - `--overwrite`：显式允许替换已有输出，默认拒绝覆盖。
- Dry-run 至少报告：输入消息数、状态消息数和频率、实际名称集合、各反馈通道最小/最大/裁剪数、20 个目标关节和父子边、已有 TF 冲突、时间戳回退数、预计新增 `TFMessage` 和 `TransformStamped` 数量。
- 所有验证在创建临时输出前完成；正式输出先写入输出目录中的精确临时文件，写完后重新打开验证，再原子替换目标；失败时只清理本次临时路径。
- 输出保留每一条原始消息的原始序列化字节、bag 时间戳、相对顺序和连接元数据；新增动态 TF 使用独立、非 latched 的 `/tf` 连接。
- 输出回读必须确认：新增消息数等于有效状态消息数；每条新增消息恰有 20 个变换；20 个 child 唯一且 parent 与 URDF 一致；联合 TF 图无环、无多 parent；输入文件 SHA-256 不变。

#### 当前真实数据基线

- 指定分析输入：`test_output/01.bag` 和 `urdf_kuavo5/urdf/biped_s300053_foxglove.urdf`。
- bag 时长约 34.1 秒，共 149,771 条消息；`/dexhand/state` 为 `sensor_msgs/JointState`，共 6,673 条，平均约 196 Hz，名称集合稳定为左右手各 6 个通道。
- 当前联合 TF 中存在 `zarm_l7_link → l_palm` 和 `zarm_r7_link → r_palm` 固定边，但不存在任何手指动态边；因此 Foxglove `Transforms` 模式只能显示到手掌。
- 对当前 bag，若 6,673 条状态全部有效，预计新增 6,673 条 `/tf` 消息和 133,460 个 `TransformStamped`，输出总消息数预计为 156,444。
- 状态反馈实际未全部达到 `100`；真实验证必须确认转换忠实保留这些部分闭合值，不使用命令值或通道峰值替代。

#### 阶段门禁

- [x] 阶段一：冻结 CLI、12→20 映射表、反馈错误策略、URDF 目标集合、时间戳规则、输出不变量和最小测试夹具；先完成纯函数接口和预期样例。
- [x] 阶段二：实现 URDF 手部 revolute joint 解析、名称驱动的状态提取、`0/50/100` 角度映射及 axis-angle 变换；立即完成对应单元测试。
- [x] 阶段三：实现输入 bag/TF 只读扫描、20 个目标 child 冲突检测、手掌可达性、状态完整性和 dry-run 摘要；立即验证全部失败路径不创建输出。
- [x] 阶段四：实现逐状态生成包含 20 个变换的动态 `/tf`、原始消息保真复制、覆盖保护、临时输出、原子替换和写后回读；立即完成最小 bag 集成测试。
- [x] 阶段五：在 `VelaLoom` 环境中对指定真实 bag 先 dry-run，再生成 `test_output/issue-020/` 下的独立输出；使用 `rosbags` 检查消息/连接/字节不变量，如执行原生 ROS 检查则按规定使用已验证挂载的 `ros1_noetic` 容器。
- [x] 阶段六：使用 Foxglove `Transforms` 模式人工验收完整手指链、张开/闭合方向、部分闭合反馈、拖动和循环播放；身体、手臂、头部显示必须与转换前一致。
- [x] 阶段七：运行模块测试、全仓回归、全部脚本/测试语法检查和 `git diff --check`；按 DOD 同步 README、DECISIONS、PROGRESS 和 CHANGELOG。

#### 验收标准

- [x] 新脚本为独立模块 `scripts/add_dexhand_tf.py`，没有导入或修改其他转换脚本。
- [x] 只使用 `/dexhand/state.position` 反馈，并按消息名称正确处理乱序数组；不读取命令 topic 作为姿态。
- [x] `0/50/100` 分别映射到 URDF lower、中点和 upper，四指单通道同时驱动对应 MCP/PIP，左右镜像由 URDF axis 正确表达。
- [x] 每条有效状态消息生成一条含 20 个动态变换的 `/tf`；parent、child、translation、rotation 和时间戳均通过自动化验证。
- [x] 输入缺少目标 joint、手掌不可达、状态字段异常、非有限数值、已有目标 TF、多 parent 或环路时安全失败且不创建输出。
- [x] 输入 bag 和 URDF 不变；原始动态 `/tf`、`/tf_static` 和全部非 TF 消息的原始字节、时间戳、顺序、数量及连接元数据保持不变。
- [x] dry-run、默认覆盖保护、显式 `--overwrite`、输入输出同路径保护、失败清理、原子替换和写后回读全部通过。
- [x] 当前真实 bag 输出包含预期 6,673 条新增 `/tf` 和 133,460 个手指变换，联合 TF 图无环、无多 parent、20 个手指 child 均可从左右手掌到达。
- [x] Foxglove 使用同一 URDF、`Transforms` 模式及 `base_link`/`odom` Fixed frame 时，手指按实际反馈运动；反馈未到 `100` 时不会被显示成完全闭合；身体、手臂和头部不受影响。
- [x] 所有测试输出仅位于 `test_output/issue-020/`；模块测试、全仓回归、语法检查、真实数据验证、Foxglove 人工验收和 `git diff --check` 全部通过后才能标记 `DONE`。

## 新增 Issue 模板

复制以下结构，分配新的编号后追加到本文件：

```markdown
### ISSUE-XXX：<简短标题>

- 状态：`TODO`
- 优先级：P1/P2/P3
- 目标：
- 依赖：无 / ISSUE-XXX（如有依赖必须等待前置 Issue 完成）
- 修改边界：
- 验收标准：
  - [ ]
```
