# 技术决策记录

## D001：使用 `.ai/` 作为 AI 协作目录

- 日期：2026-08-25
- 决策：将 AI 开发规范、Issue、架构、决策和进展统一放入 `.ai/`。
- 原因：目录语义明确，便于 AI 作为项目入口读取。

## D002：任务使用单一 `TASK.md` 管理

- 日期：2026-08-25
- 决策：所有开发任务使用 Issue 编号记录在 `.ai/TASK.md`，每次开发只执行一个 Issue。
- 原因：保证任务边界清晰，避免并行修改造成接口、文件和进展记录冲突。

## D003：frame_id 映射使用重复的 `--map`

- 日期：2026-08-25
- 决策：第一版使用 `--map TOPIC=FRAME_ID`，暂不加入 YAML 配置。
- 原因：命令行直接、依赖少，适合当前规模。

## D004：输出默认不覆盖

- 日期：2026-08-25
- 决策：默认遇到重名追加 `_loom_YYYYMMDD_HHMMSS`，只有显式 `--overwrite` 才覆盖。
- 原因：保护原始处理结果，降低批处理误覆盖风险。

## D005：暂不提供通用 TF frame 重写模式

- 日期：2026-08-26
- 决策：`sync_frameid.py` 继续只处理普通消息的顶层 `message.header.frame_id`，不将 `/tf` 或 `/tf_static` 纳入通用 `--map`；如未来需要修改 TF，必须设计独立模式和独立映射规则。
- 证据：真实 rosbag 中 `/tf` 消息为 `tf2_msgs/TFMessage`，frame 位于 `transforms[].header.frame_id` 和 `transforms[].child_frame_id`；`/tf_static` 还存在多条连接和静态变换集合，不能按普通 Header topic 等价处理。
- 兼容性：全局替换父/子 frame 可能破坏 TF 树连通性、引入重复 child frame、改变静态/动态语义，或使传感器 topic 与 TF 不一致。
- 风险控制：当前遇到 `/tf` 的普通映射会明确报错并清理临时输出，输入保持不变；新增回归测试锁定该安全边界。未来若有明确 TF 需求，另建 Issue，定义 parent/child 映射、冲突检测、时间语义和全量 TF 树验证。

## D006：`--map` 支持一次接收多个映射

- 日期：2026-08-26
- 决策：将 `sync_frameid.py` 的 `--map` 解析为一个或多个连续的 `TOPIC=FRAME_ID` 值，遇到下一个选项时结束；保留重复 `--map` 的旧语法。
- 原因：相机 topic 映射通常成组出现，减少重复书写，同时保持已有脚本调用兼容。
- 限制：当前 CLI 没有裸子命令，因此“结束边界”定义为下一个以 `--` 开头的选项；未来引入子命令时需单独设计其边界解析。

## D007：将 rosbag ↔ URDF 对齐拆为串行专项 Issue

- 日期：2026-08-26
- 决策：将 10 条独立修复建议拆为 ISSUE-005 至 ISSUE-014，严格串行推进，每个 Issue 只讨论一个问题。
- 原因：当前问题同时涉及命名、拓扑、时间、数据来源和几何外参；先统一接口契约可以避免在不同问题上重复修改或引入两套 TF 发布权。
- 约束：本阶段仅更新 `.ai/` 规划和基线记录，不修改 URDF、rosbag 或程序代码；每个后续 Issue 完成前必须保留原始 bag 不变。
- 当前状态：ISSUE-005 为下一步讨论入口，尚未进入 `IN_PROGRESS`，等待用户逐项确认 TF 发布权和根节点方案。

## D008：ROS1 操作统一使用 `ros1_noetic` 容器

- 日期：2026-08-27
- 决策：需要 ROS1/ROS Noetic runtime 的 ROS、rosbag、ROS 消息、节点、构建和测试操作统一在 Docker 容器 `ros1_noetic` 中执行；非 ROS 项目命令继续使用 `VelaLoom` conda 环境。
- 原因：容器已提供 Ubuntu 20.04 和 ROS Noetic，保证 ROS1 工具链一致，同时避免把 ROS runtime 依赖混入项目 conda 环境。
- 约束：容器仅作为执行载体；使用前确认工作区挂载，必要时启动容器并加载 `/opt/ros/noetic/setup.bash`；输入、输出和日志必须通过挂载路径保留在宿主机工作区。

## D009：统一 TF 输出 bag 使用相机桥接单位变换

- 日期：2026-08-27
- 决策：新增的统一 TF 输出 bag 转换工具保持 URDF 和原始 bag 不变，只在输出 bag 的规范化 `/tf_static` 中补齐 URDF 相机安装固定边，并增加 `camera_base → cam_h_link`、`l_d405_camera_base → cam_l_link`、`r_d405_camera_base → cam_r_link` 三条单位桥接。
- 头部层级：输出 bag 采用 `zhead_2_link → camera_base → cam_h_link`；原始 `zhead_2_link → head_camera_base → head_camera_depth` 作为旧链，若有消息引用则不得静默删除。
- 原因：用户已确认三组坐标系重合；将驱动 frame 作为 URDF 机械 frame 的同位子节点，可以在不改写图像 optical frame 的前提下，把相机数据接入唯一 `odom` 根。
- 约束：`/tf` 动态消息原样复制并保留 `odom → base_link`；静态边按 `parent, child` 去重；冲突、重复 parent 或旧 frame 引用必须显式失败；原始 bag SHA-256 必须保持不变。

## D010：统一 TF 输出采用单条规范化静态消息

- 日期：2026-08-27
- 决策：`unify_rosbag_tf.py` 只输出一条 latched `/tf_static` 消息；相同 `parent→child` 且位姿一致的输入边合并，位姿冲突或同一 child 多 parent 直接失败。默认移除旧头部链，只有 `--keep-legacy-head-chain` 显式保留；若普通消息引用旧 frame，默认失败而不静默删除。
- 原因：消除重复静态发布来源，保证合并动态和静态边后 TF 根集合为 `{odom}`，并避免破坏仍在使用旧 frame 的数据。
- 约束：固定安装边只从传入 URDF 读取，不在脚本中重复硬编码位姿；三条桥接边固定为单位变换。

## D011：开发测试文件输出统一使用 test_output

- 日期：2026-08-27
- 决策：所有开发测试和验证主动生成的夹具、临时文件、转换结果、bag、日志、报告、截图及中间产物统一写入仓库根目录 `test_output/`，并按 Issue 或测试模块划分子目录；禁止使用系统 `/tmp`、仓库外个人路径、`rosbag/`、`urdf*/` 或源码目录作为测试输出位置。
- 原因：集中管理测试产物，便于人工复核、失败清理、数据安全检查和跨宿主机/ROS 容器定位，避免大文件散落在系统临时目录或污染输入数据目录。
- 约束：`test_output/` 必须被 Git 忽略；测试只清理由本次运行创建的精确子路径，不得清空整个目录；ROS 容器输出必须映射回宿主机同一目录；历史记录中的旧 `/tmp` 路径仅保留为事实，不再作为后续规范。

## D012：全量 URDF fixed joint 写入必须由输入绑定决策驱动

- 日期：2026-08-27
- 决策：`add_urdf_tf_static.py` 独立读取 URDF 全部直接 fixed joint；无冲突边自动补齐，任何静态位姿、不同 parent 或动态 child 冲突都必须由调用者交互选择，或由同时绑定 bag/URDF SHA-256 和完整候选集合的 decisions JSON 重放。
- 交互规则：冲突选择没有默认值；删除动态 transform 需要再次完整输入 `YES`；只有所有冲突解决后的最终写出提示使用 `[Y/n]`，Enter 默认写出。非交互写出必须同时具备完整 `--decisions-in` 和 `--yes`。
- 输出规则：最终静态边规范化为一条 latched `/tf_static`，每个 child 只有一个 parent；未经明确选择删除的动态 `/tf` 和全部非 TF 消息保持原始字节、时间戳与连接元数据；写出回读通过后才原子替换目标。
- 边界：本工具不依赖 ISSUE-015，不加入三条相机单位桥接，不处理可动 joint，也不自动合并 `head_rader`/`head_radar` 等相似名称。
- 原因：全量 fixed joint 可能与现场驱动 TF 的发布权、parent 或标定位姿冲突；把选择显式化并绑定具体输入，可以避免旧决定跨数据误用及静默破坏 TF 树。

## D013：统一 TF 工具改为 bag-only 交互式森林整理

- 日期：2026-08-28
- 决策：`unify_rosbag_tf.py` 不再读取 URDF、不再增加写死的相机安装边，也不再删除旧 head camera 链；脚本只使用输入 bag 已有的动态和静态 TF 构建森林，由调用者选择目标根，并为每棵剩余树明确选择当前已合并树中的挂载 link。
- 交互规则：完整森林必须先于问题打印；包含 `map`、`odom`、`base_link` 的树按该顺序只做推荐，不自动选择；新增边统一为调用者确认的 `selected_parent → detached_root` 单位静态变换，后挂载的树可以使用已合并树中的任何 frame；多根输入在非 TTY 环境安全失败。
- 输出规则：原始静态 TF 按边和位姿去重，新增边合入后重建为一条 latched `/tf_static`；动态 `/tf` 和非 TF 记录按原始字节、时间戳、顺序和连接元数据复制。写出只在交互和 `[Y/n]` 确认完成后开始，并经同目录临时 bag 回读验证后原子替换目标。
- 原因：原 ISSUE-015 把特定 URDF 与相机命名假设固化在通用工具中，无法整理任意 bag 的 TF 森林。把拓扑选择交给调用者可消除隐式语义猜测，同时保留严格的无环、单 parent、单根和输入只读门禁。
- 限制：单位挂载仅表示调用者确认两个 frame 坐标重合；脚本不能从拓扑推断真实平移、旋转或标定外参，也不提供非交互 decisions 文件。

## D014：灵巧手动态 TF 使用反馈驱动的 URDF 耦合模型

- 日期：2026-08-29
- 决策：`add_dexhand_tf.py` 只使用 `/dexhand/state.position` 的 12 个具名反馈通道；每侧 `thumb_aux/thumb` 分别驱动 `thumbCMC/thumbMCP`，四指单通道同时驱动对应 MCP/PIP。反馈按固定 `0..100` 归一化并映射到每个 URDF joint 的 lower/upper，右手不在映射层额外反号。
- 运动学：parent、child、origin、axis 和 limit 全部从传入 URDF 读取，使用 `T_parent_child(q)=T_origin*R_axis(q)`；每条有效状态生成一条含 20 个 transform 的独立非 latched `/tf` 消息，header 时间来自状态消息，零时间回退到 bag 记录时间。
- 输出规则：输入 bag 和 URDF 只读；所有状态、URDF、TF 拓扑和冲突验证在创建临时输出前完成；原始记录的字节、时间戳、顺序和连接元数据保持不变，临时 bag 回读通过后才原子替换目标。
- 原因：当前每侧 6 个执行器反馈不能唯一恢复 10 个独立手指角度；采用已确认的线性耦合模型可以忠实显示反馈的部分闭合程度，同时不把欠驱动近似描述为真实接触姿态恢复。
