# SOMA 非镜像全库处理启动记录

| 字段 | 内容 |
| --- | --- |
| 实验 ID / 类型 | `2026-10-18-soma-full-processing`；固定参考适配、Root/候选与数据回读 |
| 开始与时区 | 全量后台启动 2026-10-18 02:07:01，Asia/Singapore；尚未结束 |
| 状态 | 已验证小批次并启动全量；最终状态以 E 盘 progress.json 为准 |
| 来源 | E:/MotionWeaverData 中的 SOMA/BONES-SEED 外部数据，不是本项目采集 |

## 范围与生效配置

当前代码基线 `9585732`，启动时 dirty，实现随后保存为 `66543d1`，新增 `data/tools/process_soma_dataset.py`、四组解析契约测试和一条审核显示释义。当前任务不训练模型，无 GPU、优化器、随机训练种子或模型对照。

元数据选择 `seed_metadata_v004.csv` 的非镜像记录：71,132 条，62,301,201 帧，最少 69 帧、最多 21,617 帧，中位 685 帧。运行前 E 卷 Healthy/OK、非 Dirty，约 1.53 TB 可用。启动前按帧数保守估计原生/固定/候选存储并保留 64 GiB 余量；原始 BVH、镜像数据和 proportional 数据不删除或改写。

固定目标为 `soma_uniform_v1`，来源统一局部轴并保留录制旋转，固定静态偏移，Root 的完整运动合入 Hips；来源轨道另存。保留原始时间和约 120 FPS，不降采样。Root/接触处理使用现有 `soma_processing_v1`，手部接触未知，参考未选中硬约束，所有任务 `training_ready=false`。操作与目录见 [全库处理说明](../soma-full-processing.md)。

Windows，Python 3.12.12、NumPy 2.5.3、SciPy 1.18.1；32 逻辑处理器、约 32 GB 内存；后台 8 个独立工作进程，单进程 BLAS 线程数 1。没有引入新依赖。

## 已执行的小批次与验证

| 运行 | 结果 | 说明 |
| --- | --- | --- |
| `20261018-soma-full-pilot-v1` | 32/32 技术通过 | 元数据等距选择，保留来源与固定输出，四进程 |
| `20261018-soma-full-pilot-v2` | 16/16 技术通过 | 八进程、动态平移拒绝及续跑检查修订 |
| `20261018-soma-full-pilot-v3` | 8/8 技术通过 | 补齐参考/任务候选索引，进度定时更新 |
| `20261018-soma-full-pilot-v4` | 4/4 技术通过，续跑仍 4/4 | 当前版本从已有回执校验哈希和共享骨架，不重复制造样本 |
| 解析与现有回归 | 四组新增解析契约、现有四组数值特征通过 | 固定偏移、Root 合成、来源参考变体、动态平移拒绝 |
| 静态与审核显示 | 关键 Ruff、既有 Node 视口/预加载通过 | 不代表模型或蒙皮效果通过 |

原生轨道与其落盘回读、固定运动与其落盘回读分别逐数组相等，NPZ 逐数组回读相等，Hips 世界位置和 Root/身体重合成门槛 `1e-9 m`。四轮试点的最大 Root/身体重合成误差为 `8.881784197001252e-15 m`。保留源参考偏移变化，不将源/目标骨长统一造成的位置变化当作精确运动重建。以上试点不是全库已验收，也不把不同版本或重叠样本数量相加当独立数据量。

当前版本的四条结果还通过既有 NativeReviewStore 完整加载：78 节点、NPZ 时间身份与接触数组长度正确，手部接触全为未知，没有代替用户记录人工通过。可提交的小型证据为[试点与续跑汇总](assets/2026-10-18-soma-full-processing/pilot-summary.json)及[全量启动快照](assets/2026-10-18-soma-full-processing/launch-snapshot.json)；启动快照记录当时完成 200 条，不代表最终结果。

## 全量执行及持久证据

```powershell
./.venv/Scripts/python.exe -u -m data.tools.process_soma_dataset --data-root E:/MotionWeaverData --run-id 20261018-soma-full-v1 --workers 8
```

本机报告为 `E:/MotionWeaverData/reports/soma_uniform/20261018-soma-full-v1/`，启动与进程记录、stdout/stderr、`resume.ps1` 位于同名 `-launch/` 目录；这些地址仅本机有效。manifest 保存原始元数据 SHA-256、目标 SHA-256 和十个数据模块的代码 SHA-256，逐条回执记录来源及全部输出哈希。运行中每约 30 秒或每 100 条更新进度，处理错误自动在首轮结束后再试一次。

当前是长批次启动记录，没有虚构最终成功率或完成时间。最终计数、未解决问题和运行时长由该批次结束时生成的 run.json、unresolved.json 和 progress.json 确定；本机源文件的人工审阅、真实接触、蒙皮及模型训练质量尚未验证。

进度目录服务与随后只读进度服务的启动均被自动审批拦截，工具只返回 `blocked by policy`，没有更具体原因。未继续尝试启动该服务；数据进程独立运行，使用既有文件日志观察。
