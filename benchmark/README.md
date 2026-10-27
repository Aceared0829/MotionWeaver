# benchmark —— 评测的唯一入口

本目录把分散在各处的评测代码收到一起，并确立一条原则：

> **官方已有的，一律复用；我们只做官方没有的补集。**

此前评测代码散落在 `training/evaluation/`、`data/tools/`、`paper/analysis/`
三处，同一指标在不同脚本里统计口径不一致（有的用均值、有的用中位、
有的用 p90），导致数字无法互相比对。本目录统一口径。

## 目录结构

```
benchmark/
  primitives/
    metrics.py       指标原语：metric() / rms() / joint_rmse_cm()
                     直接 import inference.profiling.evaluate_unreal_streaming，
                     拿不到时退回逐字等价的本地实现并标注来源
  training_metrics.py
                     官方 10 个训练指标（perplexity / commit / recons* / skate / loss）
                     只读 metrics.csv，不跑前向
  evaluate_recon.py  重建评测的**库函数**（无 argparse、可安全 import）
                     key: joint_positions_from="ric_data" + 自校验 ≤ 0.03 m
  cli_recon.py       重建评测的**命令行入口**（只做参数解析与打印）
  run.py             统一入口：一次跑完 A 组 + B 组，输出一份报告
  migrate_signature.py
                     旧 checkpoint 的签名/路径迁移（带证据校验）
  plot_arms.py       生成 paper 图 9
  ../out/            评测产物（JSON 写到 D:\MotionWeaverData\reports\）
```

**为什么把库函数与命令行拆开**：`run.py` 需要 import 库函数，若 argparse
写在模块顶层，import 时就会解析一次命令行参数，产生难以排查的副作用。

## 官方已有的能力（不要重写）

`inference/profiling/` 下 12 个脚本覆盖：

| 脚本 | 用途 |
|---|---|
| `evaluate_unreal_streaming.py` | 流式拼接 vs 定步长；`metric()`/`rms()` 的定义处 |
| `evaluate_pose_references.py` | 稀疏参考帧选帧 + `joint_rmse_cm` |
| `evaluate_action_references.py` | 起手/中间/结束姿态消融 |
| `evaluate_proxy_references.py` | 历史 24 帧 vs 已知代理前瞻 24 帧 |
| `evaluate_isolated_poses.py` | 单姿态独立重建，诊断上下文影响 |
| `evaluate_hard_constraints.py` | 硬约束三方对照 |
| `evaluate_phase_references.py` | 跑酷阶段审计 |
| `evaluate_targeted_references.py` | 问题姿态补参考 |
| `audit_marked_pose_frames.py` | 指定帧解码前后误差审计 |
| `compare_unreal_streaming.py` | 同区间比较导出包 |
| `profile_unreal_inference.py` | 单流推理延迟 |
| `pose_reference_lab.py` | 手选参考姿态实验室 |

**限制**：这些脚本都基于 UE Schema（`motion.json` + `arrays.bin`）与导出包
（`.onnx`/bundle）。我们的 SOMA 训练视图是四键 NPZ，不兼容，因此重建评测
必须写在 `soma/` 下。

## 我们的补集（官方没有的）

| 能力 | 为什么官方没有 |
|---|---|
| SOMA 四键 NPZ 上的重建 | 官方只处理 UE Schema |
| Hold / Lerp 零成本基线 | 官方不提供补间基线 |
| 四场景 × 4 档参考的窗口级统计 | 官方按片段评测，不按窗口分层 |
| 姿态误差与脚滑的分层相关性 | 官方不分析两者关系 |
| fps30 与 fps120 分开报告 | 官方只关心 UE 的 30fps |

## 两条已踩过的坑（写在这里防止重犯）

1. **`joint_positions_from="ric_data"`** —— `inverse()` 默认 `global_rot_data`，
   而 SOMA 视图直接给关节位置，必须显式指定 `ric_data`。用默认值时
   「编码→解码→还原」的往返误差高达 0.73–1.29 m，任何重建指标都无意义。
   原版在 `training/models/vqvae/motion_vqvae.py:311` 也是这么写的。

2. **自校验先行** —— 官方 `motionweaver_preview.py:416` 有一条断言：
   逆变换结果与原始动作的 RMSE 必须 ≤ 0.03 m。评测脚本必须先跑这条，
   不通过就拒绝输出重建数字。

## 统一口径

- 所有以 `_cm` 结尾的指标单位是厘米
- 所有 `metric()` 返回 `{mean, p95, max}`，`summarize()` 额外给 `median/count`
- 训练指标（`official/training_metrics.py`）是**训练集**口径，不与留出集指标混报

## 用法

```powershell
# 官方 10 指标（只读 metrics.csv）
python -m benchmark.training_metrics

# 重建评测（需要 checkpoint）
python -m benchmark.cli_recon `
  --checkpoint <ckpt> --scenes B C --clips 6
```

