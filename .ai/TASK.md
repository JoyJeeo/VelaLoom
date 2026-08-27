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

- 状态：`TODO`
- 优先级：P0
- 目标：决定 `odom`、`base_link` 的根节点关系，明确原始 `/tf`、`/tf_static` 与 `robot_state_publisher` 的唯一发布权。
- 依赖：无
- 修改边界：仅设计和外部回放方案，不修改仓库代码。
- 验收标准：
  - [ ] 明确唯一 TF 根和每类 TF 的唯一发布者；
  - [ ] 明确播放时保留/过滤的 TF topic；
  - [ ] 没有重复 child frame、TF 冲突或时间回退。

### ISSUE-006：建立 29 个身体关节的 JointState 映射

- 状态：`TODO`
- 优先级：P0
- 目标：把 `/sensors_data_raw.joint_data.joint_q` 或 `/joint_cmd.joint_q` 显式映射成 URDF joint name，并驱动主体动作。
- 依赖：ISSUE-005
- 修改边界：优先在仓库外建立适配器/启动配置。
- 验收标准：
  - [ ] 29 个数组元素的顺序、单位、方向和时间戳已确认；
  - [ ] 双腿、腰、双臂和头部动作连续且正确；
  - [ ] 主体 TF 无重复发布。

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

- 状态：`TODO`
- 优先级：P1
- 目标：补齐 `zarm_l7_link → l_palm` 和 `zarm_r7_link → r_palm`，使手指链挂到手臂末端。
- 依赖：ISSUE-006
- 修改边界：外部 TF/模型方案，先不修改仓库代码。
- 验收标准：
  - [ ] 左右手掌 frame 从 `base_link` 可达；
  - [ ] 手掌安装位姿来源可追溯。

### ISSUE-011：评估并处理 12→20 手指关节映射

- 状态：`TODO`
- 优先级：P1
- 目标：确定 `/dexhand/state` 的 12 个通道是否能通过耦合模型驱动 URDF 的 20 个手指关节。
- 依赖：ISSUE-006、ISSUE-010
- 修改边界：不得把无法从 bag 观测到的 20 个真实角度假设为已知。
- 验收标准：
  - [ ] 给出 12 个通道与 20 个 joint 的明确映射或证明不可唯一恢复；
  - [ ] 近似方案记录比例、偏置、符号和误差；
  - [ ] 完美方案列出需要补录的 20 个角度接口。

### ISSUE-012：补齐左右 D405 相机安装和传感器 TF

- 状态：`TODO`
- 优先级：P1
- 目标：补齐左右 `l_d405_camera_base`、`r_d405_camera_base` 及其 optical frame 到机器人主体的 TF。
- 依赖：ISSUE-006、ISSUE-010
- 修改边界：使用可追溯的安装标定外参，不凭空估计。
- 验收标准：
  - [ ] `base_link → l_d405_camera_base` 查询成功；
  - [ ] `base_link → r_d405_camera_base` 查询成功；
  - [ ] 彩色/深度图像 frame 与对应 TF 一致。

### ISSUE-013：连接并规范 cam_h/cam_l/cam_r 相机树

- 状态：`TODO`
- 优先级：P1
- 目标：处理 bag 中 `cam_h_link`、`cam_l_link`、`cam_r_link` 三棵孤立相机树，确定它们与 URDF 相机 frame 的关系。
- 依赖：ISSUE-007、ISSUE-009、ISSUE-012
- 修改边界：外部静态 TF、frame/header 适配和回放配置。
- 验收标准：
  - [ ] 不再存在孤立相机根节点；
  - [ ] 每套相机的安装外参来源明确；
  - [ ] 所有 image/CameraInfo frame 可从机器人根节点查询。

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
- 目标脚本：`scripts/add_urdf_fixed_tf.py`。
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
