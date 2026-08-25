# 项目框架设计

```text
VelaLoom/
├── scripts/                 # 可执行数据处理工具
│   ├── sync_frameid.py      # 按 topic 统一 rosbag header.frame_id
│   ├── modify_rosbag_camera_frames.py
│   └── modify_lr_optical_frames.py
├── rosbag/                  # 记录数据
├── urdf_kuavo5/             # ROS 包、URDF、网格和机器人资产
├── assets/                  # 静态资源
└── .ai/                     # AI 开发协作规范
```

## 数据处理边界

脚本采用“输入只读、输出新文件”的转换模式：读取源数据 → 校验消息 → 转换指定字段 → 写入新数据 → 输出统计。通用脚本不得依赖某一个 bag 的绝对路径。

## rosbag 工具约定

- ROS1 bag 使用 `rosbags.highlevel.AnyReader` 读取，使用 `rosbags.rosbag1.Writer` 写出；
- topic 默认使用完整字符串匹配；
- 普通带 Header 的消息修改 `message.header.frame_id`；
- `/tf` 和 `/tf_static` 的父子坐标修改必须单独设计和验证；
- 输出先写临时文件，成功关闭后再移动到最终路径。
