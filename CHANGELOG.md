# Changelog

本文件记录面向用户或开发流程有实际影响的特性、修复和规范变更。每个特性必须在交付时追加一条记录，说明改了什么以及为什么改。

## Unreleased

- **ROS1 执行环境约定**：需要 ROS Noetic runtime 的 ROS/rosbag 操作统一在 `ros1_noetic` Docker 容器中执行；非 ROS 命令继续使用 `VelaLoom` conda 环境，容器输入输出通过宿主机工作区挂载保存。
- **开发流程规范**：增加单模块变更边界、特性分支开发和仅在 `main` 提交/推送的规则；明确自动化验证才是功能门禁，变更日志仅用于发布和交付追踪。
- **阶段化开发规范**：要求 Issue 按阶段实现、按阶段测试，当前阶段通过后自动推进下一阶段，禁止全部实现完成后才集中测试。
- **ISSUE-003**：明确 `sync_frameid.py` 暂不支持通用 `/tf`、`/tf_static` frame 重写；遇到 TF 映射时安全失败并保持输入不变。
- **ISSUE-004**：`sync_frameid.py` 的 `--map` 现在可一次接收多个连续映射，同时兼容重复 `--map` 写法，减少批量相机映射命令的重复书写。
- **ISSUE-015**：新增 `unify_rosbag_tf.py`，从 URDF 补齐相机安装固定边，加入三条单位相机桥接，去重并重建 latched `/tf_static`，同时保持动态 `/tf`、非 TF 消息和输入 bag 不变。
- **ISSUE-016**：新增独立的 `add_urdf_tf_static.py`，读取 URDF 全部 fixed joint，对静态、动态和多 parent 冲突要求交互或哈希绑定 decisions 明确选择，再原子写出唯一 latched `/tf_static`；避免自动猜测 frame 语义或误删动态 TF。
- **ISSUE-017**：将 `unify_rosbag_tf.py` 修复为仅基于 bag 的交互式 TF 森林整理工具；移除 URDF 和写死相机规则，由调用者选择目标根与每棵树的挂载 link，并在写出前后执行完整拓扑和数据保真验证。
- **ISSUE-018**：将 `unify_rosbag_tf.py` 树形日志和新增边摘要中的 `[D]`、`[S]`、`[B]` 来源标记统一移到 frame 或边名称末尾，提高层级名称的可读性。
- **ISSUE-019**：为 `unify_rosbag_tf.py` 的完整森林和子树日志增加 `├──`、`└──`、`│` 层级连接线，明确显示父子关系、同级分支和分支延续。
- **测试输出规范**：所有开发测试生成的文件统一写入仓库根目录 `test_output/`，并将该目录排除在 Git 之外，避免系统临时目录和输入数据目录散落测试产物。
