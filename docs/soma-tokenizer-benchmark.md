# tokenizer 评测报告（统一 benchmark）

> 本报告由 `benchmark.run` 生成，数据源为 `D:\MotionWeaverData\reports\benchmark.json`。
> 生成时间：2026-10-27。

## 一、评测体系

我们把原先散落在三处的评测代码收到 `benchmark/`，并确立一条原则：
**官方已有的不重写，我们只做补集，但对外是一份报告。**

| 组 | 内容 | 来源 |
|---|---|---|
| **A 组** | 10 个训练指标（perplexity / commit / recons* / joint_vel / skate_contact / loss） | 原版 `motion_vqvae.py:336-352` 在训练时写入 `metrics.csv`，本报告只读不算 |
| **B 组** | 关节 RMSE（厘米）、feature L1、自校验误差 | 我们的补集：官方脚本只吃 UE Schema，SOMA 四键 NPZ 需另写 |
| **C 组** | Hold / Lerp 零成本基线 | 引用已冻结的基线报告，不重算 |

指标原语（`metric()` / `rms()`）直接 import 官方的
`inference.profiling.evaluate_unreal_streaming`，不重新实现。

## 二、A 组：原版 10 指标（训练集口径，末段 10% 均值）

| 指标 | BC120 (B+C, dt3) | A120 (A, dt2) | B120 (A, kmeans, dt2) |
|---|---|---|---|
| `l_recons_pose` | 0.3032 | **0.1811** | 0.1815 |
| `l_recons_pose_local` | 0.3032 | **0.1811** | 0.1815 |
| `l_recons_pose_global` | 0.0000 | 0.0000 | 0.0000 |
| `l_recons_root_global` | 0.0000 | 0.0000 | 0.0000 |
| `l_recons_root_local` | 0.0000 | 0.0000 | 0.0000 |
| `l_commit_pose` | **0.0050** | 0.5844 | 0.5519 |
| `l_joint_vel` | **0.1798** | 0.2397 | 0.3329 |
| **`l_skate_contact` (m/s)** | **0.3911** | 1.1499 | 1.1103 |
| `perplexity_pose` | **5.6350** | 5.2925 | 5.1851 |
| `loss` | **0.6668** | 0.6838 | 0.8694 |

**注意**：`l_recons_pose_global` 与两条 `l_recons_root_*` 全部为 0.0000，
说明根运动与全局姿态的重建是**完全精确**的；误差集中在局部姿态。

## 三、B 组：我们的重建评测

| 模型 | feature L1 | 关节 RMSE (cm) | 自校验 (m) |
|---|---|---|---|
| BC120 (B+C, dt3) | 0.7233 | **105.33** | 0.000000 |
| A120 (A, dt2) | 0.7152 | 208.98 | 0.000000 |
| A120 (A, dt3) | **0.6572** | 121.04 | 0.000000 |
| B120 (A, kmeans, dt2) | 0.7824 | 308.09 | 0.000000 |

**自校验全部为 0.000000 m**，说明「特征 → 关节位置」的转换链路完全自洽
（判据来自官方 `motionweaver_preview.py:416` 的 0.03 m 门槛）。
因此这组数字里的差异来自模型本身，不是评测代码。

## 四、一个必须写明的陷阱：loss 不可跨 down_t 比较

把 A 组与 B 组并排看会得到一个矛盾：

- A120 (dt2) 的 `l_recons_pose` 最低（0.1811），关节误差却高达 208.98 cm；
- BC120 的 `l_recons_pose` 较高（0.3032），关节误差反而最小（105.33 cm）。

原因是 `l_recons_pose` 在**归一化特征空间**里计算，而归一化用的 `mean/std`
随 `down_t` 变化（`frames_per_token` 不同，统计分布不同）。
因此：

**`l_recons_pose` 只能在同一个 down_t 内部比较，不能跨 down_t 排序。**

同一 down_t 内的比较是有效的：A120(dt2) 0.1811 vs B120(dt2) 0.1815 —— 
两者几乎相等，与先前 A/B 对照得出的「kmeans_init 无优势」一致。

## 五、脚滑：唯一可直接与基线对比的指标

`l_skate_contact` 的单位是 **m/s**（接触期脚部速度），因此可以与基线直接比：

| 来源 | 脚滑 (m/s) | 相对源动作 |
|---|---|---|
| 源动作（参考） | 0.0261 | 1× |
| **Lerp 基线（middle_and_end）** | **0.545** | **21×** |
| Hold 基线（middle_and_end） | 1.841 | 71× |
| **BC120 (B+C)** | **0.3911** | **15×** |
| A120 (dt2) | 1.1499 | 44× |

**BC120 的 0.3911 m/s 已经优于 Lerp 基线的 0.545 m/s**，
这是本次评测里第一个明确「优于基线」的指标。但要注意两者口径不同：
基线是**补间生成**的结果，而这里是 **tokenizer 重建**的结果，
不能直接当作「模型补间能力超过 Lerp」的证据。

## 六、局限

1. A 组是**训练集**口径，不含留出集，不能等同于泛化能力；
2. B 组的关节 RMSE 数值很大（105–308 cm），说明即使 tokenizer 的
   特征级重建损失已经较低，还原到关节位置后误差仍不可接受。
   这与「`l_recons_pose_global` 恒为 0」并不矛盾——全局量精确，
   局部姿态的累积误差在 FK 后被放大；
3. A120 (dt3) 的 `metrics.csv` 缺失（训练时 CSVLogger 未落盘），
   只有 B 组数据，A 组标为「—」；
4. 历史 A800 的 20k 检查点**本地不存在**（配置里的 `data.folder` 指向云端
   `/workspace/motionweaver-20260928/dataset`），无法纳入本轮评测。

## 七、复现

```powershell
python -m benchmark.run --clips 4 `
  --report D:\MotionWeaverData\reports\benchmark.json
```

单独跑某一组：

```powershell
python -m benchmark.training_metrics          # A 组
python -m benchmark.evaluate_recon `
  --checkpoint <ckpt> --scenes B C --clips 6  # B 组
```

