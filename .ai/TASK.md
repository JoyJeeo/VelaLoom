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
