# 开发进展

更新时间：2026-08-26

## 已完成

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

## 开发流程规范整改记录（2026-08-26）

- 新增根目录 `CHANGELOG.md`，作为每个特性或修复必需的交付记录，记录变更内容和原因；它不替代测试门禁。
- 更新 `DEVELOPMENT_WORKFLOW.md`、`DOD.md`、`TESTING.md` 和 `.ai/README.md`：增加单模块变更边界、特性分支隔离、仅在 `main` 提交/推送、模块文件头 Purpose/Input/Output/Example、测试与回归门禁及无 `TODO` 交付要求。
- 验证：`git diff --check` 通过；本次仅修改开发规范和变更记录，未创建分支、提交或推送，保留工作区既有本地变动。

## 测试门禁边界修正（2026-08-26）

- 将 `CHANGELOG.md` 明确定义为发布和交付追踪材料，不再作为功能正确性或测试通过的门禁项。
- 修正 DOD：`CHANGELOG.md` 必须出现在每个特性/修复的交付记录中，但仍不作为测试通过的依据。
- 明确真正的功能门禁是自动化测试、集成/端到端测试，以及适用的语法、静态、构建和数据安全检查。

## 更新规则

每完成一个 Issue，追加日期、Issue 编号、变更文件、验证命令、结果和遗留问题。保留历史记录，不删除已完成条目。
