# 工具入口索引

> 状态：阶段 3 已完成，入口以分组后的模块路径为准；旧路径 shim 仍可用。
> 目标：先回答“这件事该用哪个入口”，再决定文件是否需要搬家。

## 正式入口

| 任务 | 入口 | 说明 |
| --- | --- | --- |
| 正式评测 | `python -m benchmark.cli_recon`、`python -m benchmark.run` | 正式数字以 `benchmark/` 为准；`evaluate_all()` 是库入口 |
| refpose 训练 | `python -m training.pretrain.train_conditioned_pose` | 读取冻结契约；模型与窗口已抽离 |
| 轻量训练对照 | `python tools/train_reference_pose.py` | 跳过 audit/review/freeze 的快速入口 |
| UE 推理包导出 | `python -m inference.export.export_unreal_inference` | 无损 FP32 推理包 |
| refpose ONNX 导出 | `python benchmark/manifest/export_onnx.py` | 静态 batch=1，见 [阶段 1 记录](refpose-stage1-onnx-export.md) |
| 数据训练视图构建 | `python -m data.tools.build.build_soma_training_set` | SOMA processed → 四键 NPZ / Schema v3 |
| 审核服务 | `python -m data.tools.review.motion_review_server` | 本机只读审核，人工结论独立保存 |
| 数据侧评测编排 | `python -m data.tools.eval.run_soma_eval` | 训练视图 → 契约 → 评测 → 汇总 |

## 安装后命令

`pip install -e .` 后会生成以下入口，可从任意工作目录运行：

| 命令 | 等价模块入口 |
| --- | --- |
| `mw-benchmark` | `python -m benchmark` / `benchmark.run:main` |
| `mw-review` | `python -m data.tools.review.motion_review_server` |
| `mw-train-refpose` | `python -m training.pretrain.train_conditioned_pose` |
| `mw-export-unreal` | `python -m inference.export.export_unreal_inference` |

## 实验入口（冻结新增）

| 目录 | 定位 |
| --- | --- |
| `training/evaluation/` | 历史训练评估、容量诊断与评估画廊；新正式指标不再放这里 |
| `inference/profiling/` | UE Schema / 导出包实验评测；其中测试应移入 `tests/` |
| `inference/visualization/` | 一次性测量仪器；见 [现状与边界](refpose-visualization-instruments.md) |
| `docs/experiments/assets/` | 历史实验代码与证据；不随产品包发布 |

## 运维与数据流水线

| 任务 | 入口 |
| --- | --- |
| UE 批次队列 | `python -m data.tools.ops.run_seed_queue` |
| 通宵流水线 | `python -m data.tools.ops.run_overnight_seed_pipeline` |
| 批次关闭与清理 | `python -m data.tools.publish.close_seed_batch`、`python -m data.tools.publish.cleanup_seed_batch` |
| 迁移进度 | `python -m data.tools.ops.migration_progress_server`、`python -m data.tools.ops.migration_progress_lite_server` |
| 长任务脱离 | `tools/run_detached.sh` |

## 兼容入口

| 路径 | 说明 |
| --- | --- |
| `tools/compat/motionbricks/` | 旧 `motionbricks.*` 导入与检查点兼容；冻结，不复制网络实现 |
| `data/runtime/unreal_quality.py` | 旧 `motionbricks.data.unreal_quality` 别名；属于兼容例外 |
| `data/tools/<name>.py` | 全部实现已迁入 `common` / `review` / `ingest` / `build` / `audit` / `publish` / `ops` / `eval`；旧路径由 `data.tools._compat` 转发，模块身份不变 |
| `tools/archive/` | 一次性迁移脚本，不再维护 |

## data/tools 分组

| 分组 | 职责 |
| --- | --- |
| `common/` | 文件锁等跨工具基础库 |
| `review/` | 审核服务、原生/条件审核存储、拒绝库、`review_web/` |
| `ingest/` | 导入、重定向、骨架冻结、批次准备 |
| `build/` | 训练视图、索引、契约、refpose 发布包 |
| `audit/` | 只读审计、质量筛查、训练前校验 |
| `publish/` | 封版、批次关闭、提升与清理 |
| `ops/` | 队列、通宵流水线、迁移、恢复 |
| `eval/` | 数据侧评测编排 |

## refpose 代码归属

| 职责 | 位置 |
| --- | --- |
| 模型与损失 | `motionweaver.refpose`（`model/MotionWeaver/refpose/`） |
| 封版契约校验 | `data.runtime.refpose_contract` |
| 窗口加载与采样 | `data.runtime.refpose_windows` |
| 训练循环与 CLI | `training.pretrain.train_conditioned_pose` |
| Root 计划头训练脚本 | `tools/train_root_plan.py` |

旧名称 `training.pretrain.train_conditioned_pose.ReferenceGuidedPose`、
`MotionWindows`、`losses` 仍可用，但新代码应直接导入上表位置。

## 待整理

- 旧路径 shim 至少保留一个版本周期；删除前先确认外部脚本不再引用。
- `inference/profiling/` 与 `inference/visualization/` 已冻结新增，只修正确性缺陷。
- 新增入口时先更新本表，再决定是否移动文件。
