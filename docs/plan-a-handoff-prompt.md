# MotionWeaver 方案 A 收尾任务 · 执行提示词

> 用途：把这份文本整段发给新的 agent 会话，让它自主完成方案 A 的收尾。
> 所有数字均为实测值，出处见文末「证据来源」。

---

## 一、你的任务（一句话）

**只做方案 A（关节位置损失）。一轮一轮试，留下好的优化、淘汰坏的，自己训练、自己评测、自己分析，直到方案 A 彻底跑完并给出最终结论。**

不要问用户要不要继续，不要中途停下来汇报请示。用目标模式 + 定时任务自己推进。

---

## 二、项目现状

### 2.1 代码与环境

| 项 | 值 |
|---|---|
| 仓库 | `D:\AILocomotonSystem\MotionWeaver` |
| 分支 | `codex/uefn-soma-retarget` |
| 最新提交 | `6382b15` feat(train): add an optional metric joint-position loss |
| Python | `.venv\Scripts\python.exe`（必须用这个） |
| 数据 | `D:\MotionWeaverData\soma_train_v1\full-v1-f32`（四键 NPZ） |
| 训练输出 | `D:\MotionWeaverData\training_runs\<run_name>` |
| 日志 | `D:\MotionWeaverData\logs\<run_name>.log` |
| 硬件 | RTX 4070 Laptop 8GB，实测 bs=4 占 3.3GB |

**纪律：训练数据与权重一律在 D 盘，绝不碰 E 盘。**

### 2.2 数据规模（关键！）

| 范围 | 条数 | 说明 |
|---|---|---|
| 全库 fps120（A+B+C+D） | **71,127** | ✅ 应该用这个 |
| **之前实际训练用的（B+C）** | **16,856** | ❌ 只占 23.7% |
| A 场景 | 28,233 | 之前完全没用 |
| D 场景 | 27,137 | 之前完全没用 |

**这是之前所有实验最大的缺陷**：只用 23.7% 数据，训 40k 步 = 12 epoch，全程在过拟合区。

数据加载方式：
```python
from data.runtime.soma_realtime_dataset import SomaRealtimeDataset
# 全库（不传 scenes 即可）：
ds = SomaRealtimeDataset(views, window_frames=72, fps_group="fps120")
# 只 B+C：
ds = SomaRealtimeDataset(views, window_frames=72, fps_group="fps120", scenes=["B","C"])
```

### 2.3 模型与 token 约束

| 项 | 值 |
|---|---|
| 参数量 | 35,911,021（down_t=3） |
| `down_t` | 3 → `frames_per_token = 8` |
| `min_tokens` / `max_tokens` | 6 / 16 |
| 72 帧 → token 数 | 9（合法，A/D 也合法） |
| `nb_code` | 1,099,511,627,776（= 32^8，per_head=32） |
| `quantizer_mu` | 0.9（官方默认 0.8） |
| `threshold_ema_dead_code` | 0（已关闭死码重置） |

### 2.4 已实测的干净对照（**这是最重要的基础数据**）

全部用官方脚本 `benchmark/evaluate_recon.py` 的 `evaluate_all(rows=(8,), clips=16)` 测得，指标是 `mean_joint_rmse_cm`（真 RMSE，单位厘米）。

| 配置 | 步数 | 数据 | `joint_rmse` | 判读 |
|---|---|---|---|---|
| 旧cfg 无熵 无关节 | 10,128 | B+C | 94.91 | 原始基线 |
| 新cfg 无熵 无关节 | 10,120 | B+C | **94.54** | 配置改动无影响（−0.4%） |
| 新cfg **有熵** 无关节 | 10,120 | B+C | **100.30** | 熵**+6.1% 变差** |
| 新cfg **有熵+关节** | 10,000 | B+C | **104.71** | 关节**再 +4.4% 变差** |
| 新cfg 无熵 无关节 | 40,000 | B+C | 108.76 | 过拟合 |
| 新cfg 有熵 无关节 | 40,000 | B+C | 103.29 | 过拟合中熵显得有益 |
| 新cfg 有熵+关节 | 39,991 | B+C | 108.23 | — |

**三条硬结论（同步数对照得出，不要推翻）：**

1. **配置改动（per_head 10→32、mu 0.99→0.9）无影响**：94.91 → 94.54，差 0.4%。
2. **熵正则在欠拟合区有害**：10k 同步数下 94.54 → 100.30（+6.1%）。
3. **关节位置损失也有害**：10k 同步数下 100.30 → 104.71（+4.4%）。

**注意**：40k 那三行都在过拟合区（12 epoch），**不能**用来判断熵或关节损失的真实效果。40k 有熵 103.29 好于 40k 无熵 108.76，这个「熵有益」是过拟合区间的假象，与 10k 的结论矛盾——原因就是数据量不足。

### 2.5 全库训练的最新状态（未完成）

| 项 | 值 |
|---|---|
| run | `full_noloss_40k` |
| 数据 | **71,127 条全库**（A+B+C+D） |
| 配置 | 无熵、无关节损失 |
| 实际步数 | **37,792**（被手动终止，未到 40,000） |
| 可用 checkpoint | `epoch=1-step=20000.ckpt`、`last.ckpt` |
| **无 final.ckpt** | 20k 权重 ≠ 37,792 步状态 |
| 训练指标（37k 步） | `l_recons_pose=0.2745`、**`perplexity_pose=8.29`** |

**一个正面信号**：全库下 `perplexity` 达到 8.29，而 B+C 上塌缩时只有 1.29（容量 32，从 4% 用到 26%）。**数据量确实缓解了码本塌缩。**

**但关节精度未知**——还没评测过全库模型。

---

## 三、方案 A 是什么

### 3.1 目标

让训练损失直接看见「米制关节误差」，从而与验收指标对齐。

原来的问题：训练损失是 941 维归一化特征的平均 SmoothL1（约 0.28），而验收是米制关节 RMSE。两者差距过大，训练损失降了不代表关节精度提升。

### 3.2 实现（已提交在 `6382b15`）

在 `training/models/vqvae/motion_vqvae.py` 的 `loss()` 里，对「还原后的关节位置」加 SmoothL1：

```python
joint_pos_loss_coeff = self._args.get("joint_pos_loss_coeff", 0.0)
# ... 若 pose 是 local，pred 需先 local_to_global(to_normalize=True) 再还原
joint_pos_loss = t.nn.SmoothL1Loss()(pred_global_joints, gt_joints_pos)
losses["loss"] += joint_pos_loss_coeff * losses["l_joint_pos_m"]
```

命令行开关：`--joint_pos_loss_coeff 0.1`

### 3.3 实现过程中已修掉的致命 bug

**原 `loss()` 里 pred 与 gt 不在同一空间**：`pose=local` 时 pred 走 `local_motion_rep.inverse`，而 gt 走 `global_motion_rep.inverse`，实测相差 **120.50 cm**。已修正为 pred 先经 `local_to_global(to_normalize=True)` 再还原，往返精确到 1.2e-7（特征）/ 1.0e-6 cm（关节）。

**这个 bug 已修好并有测试锁定，不要「再发现」一次。**

### 3.4 已验证：训练口径 = 评测口径

| 量 | BC120 | jointpos_40k |
|---|---|---|
| pred(训练口径) vs gt(训练口径) | 72.14 | 77.32 |
| pred(评测口径) vs gt(评测口径) | **72.14** | **77.32** |

两组数**精确相等**。gt 之间差 64.07 cm 是整体旋转，但 pred 与 gt 同步旋转，是**等距变换**，误差不变。

**结论：方案 A 优化的量就是评测的量，损失形式没有错。不要再怀疑口径。**

### 3.5 梯度已验证足量

只反传关节位置损失时：encoder 梯度范数 1.17、decoder 20.39，比重建损失的梯度**大 3.6 倍**。

**结论：梯度没问题。不要再查「梯度是不是没传到」。**

---

## 四、根据现有证据，方案 A 当前的核心问题

**不是损失形式错，不是梯度问题，不是口径问题。**

**而是：之前所有实验都在 16,856 条数据（23.7%）上做，40k 步 = 12 epoch，全程过拟合。过拟合掩盖了真实效果。**

关节位置损失在过拟合区强行拟合训练窗口的关节位置，等于加剧记忆训练集，所以变差。

**因此：必须先把数据换成全库 71,127 条，在全库上重测方案 A。** 全库 40k 步只有约 2.2 epoch，过拟合会大幅缓解。

---

## 五、接下来要做什么

### 阶段 0：先测清全库基线（第一步，最优先）

已有 `full_noloss_40k` 的 20k checkpoint。先用官方脚本评测它：

```powershell
cd 'D:\AILocomotonSystem\MotionWeaver'
.\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, r'D:\AILocomotonSystem\MotionWeaver'); from benchmark.evaluate_recon import evaluate_all; r = evaluate_all(r'D:\MotionWeaverData\training_runs\full_noloss_40k\checkpoints\epoch=1-step=20000.ckpt', rows=(8,), clips=16); print(r['mean_joint_rmse_cm'])"
```

**判据**：若结果显著优于 94.54，说明数据量是关键，后续全部在全库上做。

### 阶段 1：建立全库基线

跑全库、无熵、无关节损失，40,000 步 → `full_base_40k`

```powershell
$bat = @'
@echo off
cd /d D:\AILocomotonSystem\MotionWeaver
set PYTHONIOENCODING=utf-8
.venv\Scripts\python.exe -u -m training.pretrain.train_unreal --model vqvae --soma_views "D:\MotionWeaverData\soma_train_v1\full-v1-f32" --soma_window_frames 72 --soma_fps_group fps120 --num_workers 4 --down_t 3 --nb_code_per_head 32 --output "D:\MotionWeaverData\training_runs\full_base_40k" --max_steps 40000 --batch_size 4 --accelerator gpu --precision 32 --checkpoint_every 10000 > "D:\MotionWeaverData\logs\full_base_40k.log" 2>&1
'@
$bat | Set-Content 'D:\MotionWeaverData\logs\run_full_base.bat' -Encoding ASCII
Start-Process cmd.exe -ArgumentList '/c','D:\MotionWeaverData\logs\run_full_base.bat' -WindowStyle Hidden -PassThru
```

**注意**：必须用 `cmd.exe /c xxx.bat` 两级脱离启动。**直接用 `Start-Process python.exe` 会在工具调用结束时被连带杀死**（已踩过三次）。

预计 25–30 分钟。

### 阶段 2：在全库上试方案 A

在 `full_base_40k` 的基础上，加 `--joint_pos_loss_coeff 0.1`，其他完全相同 → `full_joint_40k`

**必须同配置、同步数**，否则对比无效。

### 阶段 3：按结果迭代

| 结果 | 下一步 |
|---|---|
| 全库 + 关节损失 **优于** 全库基线 | ✅ 方案 A 有效，扫系数 0.05 / 0.1 / 0.2 找最优，然后定稿 |
| **没有改善** | 试 0.05 / 0.3 两个端点，确认不是系数问题 |
| 仍然无改善 | 试「只在后期加」（如 20k 步后开启），避免早期干扰重建学习 |
| 出现改善但幅度小 | 加大步数或改用验证集早停 |

**「保留好的优化、淘汰坏的」**：每一项改动都必须在**同配置、同步数**下对比，有效才保留。

### 阶段 4：给出最终结论

无论有效还是无效，都要：
1. 用官方脚本统一评测所有档位
2. 写清「哪个改动有效、哪个无效、幅度多少」
3. 更新 `docs/soma-tokenizer-joint-error-options.md`
4. 提交代码（若改了训练代码）

---

## 六、不可违反的纪律（**每条都来自踩过的坑**）

### 6.1 对比实验必须同配置、同步数

**踩过的坑**：我曾拿 40k 有熵 vs 40k 无熵 得出「熵有益 5%」，但同步数 10k 下熵是**有害 6.1%**。原因是 40k 在过拟合区。

**硬约束**：任何 A/B 结论必须满足「同数据、同配置、同步数」，且避开过拟合区。

### 6.2 必须用官方评测脚本，不许自己算

**踩过的坑**：我曾自己算「平均关节距离（MAE）」却叫它 RMSE，报出 171.71 cm，而官方 `evaluate_all` 实际是 94.91 cm，差 90%。

**硬约束**：唯一判据是
```python
from benchmark.evaluate_recon import evaluate_all
r = evaluate_all(ckpt, rows=(8,), clips=16)
r["mean_joint_rmse_cm"]   # ← 这才是 RMSE
```
自校验字段 `max_self_check_m` 必须 < 0.03，否则拒绝输出。

### 6.3 不许用单步值判断训练进展

**踩过的坑**：我看到某一步 `l_joint_pos_m = 0.0996`，说「已达标」，实际末 2000 步中位数是 0.2215，标准差 0.3105（单步抖动比信号大）。

**硬约束**：看**分段均值（每 1000–2000 步）或中位数**，不看单步。

### 6.4 训练进程必须两级脱离启动

**踩过的坑**：`Start-Process python.exe` 启动的训练在工具调用结束时被连带杀死，已发生三次，最惨一次跑到 5,015 步无 checkpoint。

**硬约束**：
```powershell
# ✅ 正确
$bat | Set-Content 'xxx.bat' -Encoding ASCII
Start-Process cmd.exe -ArgumentList '/c','xxx.bat' -WindowStyle Hidden

# ❌ 错误（会被杀）
Start-Process python.exe -ArgumentList '...'
```
另：本机 `pwsh` 不在 PATH，只有 `C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe` 可用。

### 6.5 改代码必须补测试 + 变异检查

**踩过的坑**：我写过 10 个测试全绿，但把软熵 `detach()`、把符号取反、把熵项删掉——测试**依然全绿**，什么都没守住。

**硬约束**：
1. 补测试后，必须做变异检查（故意改坏，确认测试变红）
2. 变异脚本必须有 `try/finally` 还原，并校验文件哈希
3. 变异操作要用 Python 精确替换，不用 PowerShell 字符串替换（换行符不匹配会假通过）

参考现成实现：`tests/run_mutation_joint_pos.py`（已做到 8/8 全抓）。

### 6.6 不许「训练损失降了就说有效」

**踩过的坑**：`l_joint_pos_m` 训练窗口降了 32%，我据此说有效，评测实际差 3.5%。

**硬约束**：**评测指标是唯一判据**。训练损失只能说明「模型在拟合」，不能说明「泛化变好」。

### 6.7 报告数字前必须核对出处

**踩过的坑**：我曾说「根漂移 1.9 米是主因」，那是单条 clip 的结果；8 条平均是 193 cm，但换口径后又是另一回事。

**硬约束**：报任何数字前，确认①样本量 ②口径 ③是哪次 run 的哪个 checkpoint。

---

## 七、关键文件与命令速查

### 7.1 修改点

| 文件 | 作用 |
|---|---|
| `training/models/vqvae/motion_vqvae.py` | `loss()`：方案 A 的关节位置损失在这里 |
| `training/pretrain/train_unreal.py` | CLI：`--joint_pos_loss_coeff`、`--usage_entropy_coeff`、`--nb_code_per_head`、`--soma_scenes` |
| `training/configs/pretrain/motionweaver_vqvae.yaml` | 默认系数、`quantizer_mu` |
| `model/MotionWeaver/tokenizer/neural_modules/quantize_cnn_multihead.py` | 软熵、死码重置 |
| `benchmark/evaluate_recon.py` | **唯一的评测入口** |
| `tests/test_joint_position_loss.py` | 方案 A 的测试（14 项） |
| `tests/run_mutation_joint_pos.py` | 变异检查脚本（8 项） |
| `docs/soma-tokenizer-joint-error-options.md` | 结论文档，需持续更新 |

### 7.2 已有 checkpoint 清单

| 路径 | 配置 | 步数 |
|---|---|---|
| `training_runs\tok_BC120_3ep` | 旧cfg，B+C | 10,128 |
| `training_runs\ctrl_newcfg_10k` | 新cfg 无熵，B+C | 10,120 |
| `training_runs\ctrl_entropy_10k` | 新cfg 有熵，B+C | 10,120 |
| `training_runs\ctrl_noentropy_40k` | 新cfg 无熵，B+C | 40,000 |
| `training_runs\ctrl_entropy_40k` | 新cfg 有熵，B+C | 40,000 |
| `training_runs\jointpos_40k` | 新cfg 有熵+关节，B+C | 39,991 |
| `training_runs\full_noloss_40k` | 全库无熵无关节 | **37,792（无 final）** |

### 7.3 训练命令模板

```powershell
cd 'D:\AILocomotonSystem\MotionWeaver'
$run = 'full_joint_40k'
$bat = @"
@echo off
cd /d D:\AILocomotonSystem\MotionWeaver
set PYTHONIOENCODING=utf-8
.venv\Scripts\python.exe -u -m training.pretrain.train_unreal --model vqvae --soma_views "D:\MotionWeaverData\soma_train_v1\full-v1-f32" --soma_window_frames 72 --soma_fps_group fps120 --num_workers 4 --down_t 3 --nb_code_per_head 32 --joint_pos_loss_coeff 0.1 --output "D:\MotionWeaverData\training_runs\$run" --max_steps 40000 --batch_size 4 --accelerator gpu --precision 32 --checkpoint_every 10000 > "D:\MotionWeaverData\logs\$run.log" 2>&1
"@
$bat | Set-Content "D:\MotionWeaverData\logs\run_$run.bat" -Encoding ASCII
Start-Process cmd.exe -ArgumentList '/c',"D:\MotionWeaverData\logs\run_$run.bat" -WindowStyle Hidden -PassThru
```

**注意**：**不要**传 `--soma_scenes` 才是全库。

---

## 八、成功标准

方案 A 的最终结论必须能回答：

1. **在全库数据上，加关节位置损失能否降低 `joint_rmse_cm`？** 降幅多少？
2. **最优系数是多少？** 0.05 / 0.1 / 0.2 / 其他
3. **最优步数是多少？** 用全库后最优点可能上移
4. **熵正则该保留还是删除？**（10k 同步数下有害 +6.1%，需在全库上复核）
5. **最终推荐配置是什么？** 数据范围 + 步数 + 各系数

**若结论是「无效」，也要给出完整证据链**，说明排除了哪些可能。这本身就是有价值的结论。

---

## 九、证据来源

| 文件/run | 提供什么 |
|---|---|
| `reports/benchmark.json`、`benchmark_clips16.json` | 白天四档 benchmark（`joint_rmse` 92.24–316.75） |
| `training_runs/ctrl_*`（5 个） | 同步数对照 |
| `D:\tmp\check_gt_equivalence.py` | 训练/评测口径等价的证据 |
| `D:\tmp\check_gradient_reaches.py` | 梯度足量的证据 |
| `D:\tmp\evaluate_robust.py` | 24 clip 稳健评测 |
| `D:\tmp\isolate_train_eval_gap.py` | 96 case 窗口隔离实验 |
| `docs/soma-tokenizer-joint-error-options.md` | 完整分析文档 |

---

## 十、一句话交代

**之前的全部结论都建立在只用 23.7% 数据、且大多在过拟合区的基础上。现在要做的第一件事是把数据换成全库，然后客观重测方案 A——好的保留，坏的淘汰，直到给出最终答案。**
