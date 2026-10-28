# 模型权重清单（WEIGHTS MANIFEST）

> 登记 `D:\MotionWeaverData\training_runs` 下**全部需要长期保留**的权重。
> 与论文第 8 章、软著说明书对应；任何 run 名都能在此查到配置、步数与指标出处。

**保留 run：25 个　保留体积：62.67 GB　存放位置：`D:\MotionWeaverData\training_runs`**

统一评测口径：`benchmark/evaluate_recon.py` 的 `evaluate_all(ckpt, rows=(8,), clips=16)`，
scenes 默认 `("B", "C")`；所有结果 `max_self_check_m` 约 3.78e-7，远低于 0.03 门槛。

---

## A. 本次实验（修复后，全库）— 论文与软著的核心证据

| run | 说明 | 关键配置 | 最大步数 | ckpt 数 | final | 最优 joint_rmse | 体积 |
|---|---|---|---|---|---|---|---|
| `fix_base_100k` | 全库基线，100k 步，每 10k 存档 | jp=0 · ue=0 · norm=false（已修复） | 100,000 | 12 | OK | 84.50 cm @ 30000 | 4.84 GB |
| `fix_joint_100k` | 全库 + 关节损失 0.1，100k 步 | jp=0.1 · ue=0 · norm=false（已修复） | 100,000 | 12 | OK | 84.77 cm @ 60000 | 4.84 GB |
| `fix_base_40k` | 全库基线，40k 步 | jp=0 · ue=0 · norm=false（已修复） | 40,000 | 10 | OK | 83.85 cm @ 15000 | 4.02 GB |
| `fix_joint_40k` | 全库 + 关节损失 0.1，40k 步 | jp=0.1 · ue=0 · norm=false（已修复） | 40,000 | 10 | OK | 85.53 cm @ 20000 | 4.02 GB |
| `fix_base_seed7` | 全库基线换种子（种子噪声底） | jp=0 · ue=0 · norm=false（已修复） | 40,000 | 10 | OK | 85.03 cm @ 10000 | 4.02 GB |
| `fix_joint005_40k` | 关节损失 0.05 端点 | jp=0.05 · ue=0 · norm=false（已修复） | 40,000 | 10 | OK | 84.93 cm @ 15000 | 4.02 GB |
| `fix_joint030_40k` | 关节损失 0.30 端点 | jp=0.3 · ue=0 · norm=false（已修复） | 40,000 | 10 | OK | 85.21 cm @ 20000 | 4.02 GB |
| `fix_entropy_40k` | 熵正则 0.1（修复后复核） | jp=0 · ue=0.1 · norm=false（已修复） | 40,000 | 6 | OK | 85.42 cm @ 10000 | 2.42 GB |

## B. 修复前的污染对照 — 「归一化 bug」的实证

| run | 说明 | 关键配置 | 最大步数 | ckpt 数 | final | 最优 joint_rmse | 体积 |
|---|---|---|---|---|---|---|---|
| `full_base_40k` | 全库基线（污染），20k 后崩溃 | jp=0 · ue=0 · norm=true（bug） | 40,000 | 6 | OK | 105.53 cm @ 10000 | 2.42 GB |
| `full_joint_40k` | 全库 + 关节 0.1（污染） | jp=0.1 · ue=0 · norm=true（bug） | 40,000 | 6 | OK | 105.62 cm @ 10000 | 2.42 GB |
| `full_joint_005_40k` | 关节 0.05（污染） | jp=0.05 · ue=0 · norm=true（bug） | 40,000 | 6 | OK | 未评测 | 2.42 GB |
| `full_joint_030_40k` | 关节 0.30（污染） | jp=0.3 · ue=0 · norm=true（bug） | 40,000 | 6 | OK | 未评测 | 2.42 GB |
| `full_entropy_40k` | 熵 0.1（污染） | jp=0 · ue=0.1 · norm=true（bug） | 40,000 | 6 | OK | 未评测 | 2.42 GB |
| `full_noloss_40k` | 全库无熵无关节（提前终止，仅 20k） | jp=0 · ue=0 · norm=true（bug） | 20,000 | 2 | - | 未评测 | 0.81 GB |
| `jointpos_40k` | 早期 B+C + 熵 + 关节（污染） | jp=0.1 · ue=0.1 · norm=true（bug） | 40,000 | 6 | OK | 未评测 | 2.42 GB |

## C. 早期同步数对照（B+C）

| run | 说明 | 关键配置 | 最大步数 | ckpt 数 | final | 最优 joint_rmse | 体积 |
|---|---|---|---|---|---|---|---|
| `ctrl_noentropy_40k` | B+C 无熵 40k | jp=0 · ue=0 · norm=n/a | 40,000 | 4 | OK | 未评测 | 1.61 GB |
| `ctrl_entropy_40k` | B+C 有熵 40k | jp=0 · ue=0.1 · norm=n/a | 40,000 | 4 | OK | 未评测 | 1.61 GB |
| `ctrl_newcfg_10k` | B+C 新配置 10k | jp=0 · ue=0 · norm=n/a | 10,128 | 3 | OK | 未评测 | 1.21 GB |
| `ctrl_entropy_10k` | B+C 有熵 10k | jp=0 · ue=0.1 · norm=n/a | 10,128 | 3 | OK | 未评测 | 1.21 GB |
| `entropy_20k` | 早期熵正则 20k | jp=0 · ue=0.1 · norm=n/a | 20,000 | 4 | OK | 未评测 | 1.61 GB |

## D. benchmark 四臂（benchmark/run.py 硬依赖，勿删）

| run | 说明 | 关键配置 | 最大步数 | ckpt 数 | final | 最优 joint_rmse | 体积 |
|---|---|---|---|---|---|---|---|
| `tok_BC120_3ep` | B+C fps120 三档 | jp=0 · ue=0 · norm=n/a | 10,000 | 4 | OK | 未评测 | 1.61 GB |
| `tok_A120_20k` | A 场景 20k | jp=0 · ue=0 · norm=n/a | 40,000 | 9 | OK | 未评测 | 2.56 GB |
| `tok_A120_dt3_3ep` | A 场景 dt3 三档 | jp=0 · ue=0 · norm=n/a | 40,000 | 6 | OK | 未评测 | 0.80 GB |
| `tok_B120_3ep` | B 场景三档 | jp=0 · ue=0 · norm=n/a | 40,000 | 6 | OK | 未评测 | 1.71 GB |

## E. 代码注释引用的实测依据

| run | 说明 | 关键配置 | 最大步数 | ckpt 数 | final | 最优 joint_rmse | 体积 |
|---|---|---|---|---|---|---|---|
| `full_400k` | motion_vqvae.py 与 motionweaver_vqvae.yaml 注释里的 perplexity 实测 | jp=0 · ue=0 · norm=true（bug） | 40,000 | 3 | - | 未评测 | 1.21 GB |

---

## 已移除的调试 run

以下 run 属于早期环境调试（worker 数扫描、精度对照、停顿复现、冒烟测试），
**对论文与软著无证据价值**，已删除以保持目录干净。其结论已固化在
`docs/soma-training-performance-analysis.md` 与提交历史中。

| run | 用途（已不再需要） |
|---|---|
| `cmp_comp` | 对比实验残骸（无 checkpoint） |
| `cmp_orig` | 早期对比实验 |
| `entropy_5k` | 熵正则 5k 冒烟 |
| `final_smoke` | 端到端冒烟测试 |
| `p0_full_3ep` | P0 修复后 3 epoch 快速验证 |
| `p0_verify_3k` | P0 修复验证 3k |
| `p1_perhead32_3ep` | per-head 32 迁移验证 |
| `p2_bf16` | bf16 精度对照 |
| `p2_fp32` | fp32 精度对照 |
| `persist_fix` | persistent_workers 修复验证 |
| `planA_w8` | 8 worker 吞吐测试 |
| `realtime_smoke` | 实时数据集冒烟 |
| `repro_stall` | 训练停顿复现 |
| `smoke_fixed2` | 冒烟测试 v2 |
| `smoke_vqvae_100clips` | 100 clip 冒烟 |
| `speed_test_2000` | 吞吐测试 2000 步 |
| `stall_diag` | 停顿诊断 |
| `stall_w0` | worker=0 停顿复现 |
| `sweep_w0_bf16mixed` | worker=0 + bf16 扫描 |
| `sweep_w16_bf16mixed` | worker=16 + bf16 扫描 |
| `sweep_w4_bf16mixed` | worker=4 + bf16 扫描 |
| `sweep_w8_bf16mixed` | worker=8 + bf16 扫描 |
| `verify_f32` | fp32 验证 |
| `ws0_retest` | worker=0 复测 |
| `ws4_smoke` | worker=4 冒烟 |
| `ws8_nopersist` | worker=8 无 persistent 对照 |
| `ws8_smoke` | worker=8 冒烟 |

---

## 复现命令

```python
import sys
sys.path.insert(0, r"D:\AILocomotonSystem\MotionWeaver")
from benchmark.evaluate_recon import evaluate_all
r = evaluate_all(r"D:\MotionWeaverData\training_runs\fix_base_100k\checkpoints\final.ckpt",
                 rows=(8,), clips=16)
print(r['mean_joint_rmse_cm'], r['max_self_check_m'])
```
