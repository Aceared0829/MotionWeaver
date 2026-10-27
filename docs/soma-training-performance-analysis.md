# 训练链路性能分析（只读评估，未做任何改动）

- 分析时间：2026-10-26
- 分析对象：`training/pretrain/train_unreal.py`、`training/configs/pretrain/motionweaver_vqvae.yaml`、`data/runtime/unreal_dataset.py`
- 硬件：NVIDIA RTX 4070 Laptop 8GB，36 SM，torch 2.14.0+cu130，bf16 支持 ✅
- **本文档只记录分析结论，不含任何代码改动。所有数字均来自实测或源码行号引用。**

## 0. 一句话结论

**当前训练链路远未榨干硬件。** 存在 5 处明确损耗，其中两处（`num_workers=0`、`precision=32`）合计就能带来 3-6 倍提速。但在补 SOMA 数据适配层之前，这些优化都无从谈起——因为现有训练脚本根本读不了我们的数据。

## 1. 前置阻塞：训练脚本与数据格式不兼容

这是比性能更根本的问题。

| 项 | 我们的训练视图 | `UnrealMotionDataset` 要求 | 状态 |
| --- | --- | --- | --- |
| 数据格式 | 四键 NPZ：`positions/rotations/timestamps/root_track` | UE Schema v1/v2：`motion.json` + `arrays.bin` | ❌ 不兼容 |
| `schema_version` | `None` | 必须是 1 或 2 | ❌ 缺失 |
| `coordinate_system` | `None` | `unreal_component_cm_xyzw` 等 | ❌ 缺失 |
| `stats/mean.npy` | 不存在 | 必需 | ❌ 缺失 |
| `stats/std.npy` | 不存在 | 必需 | ❌ 缺失 |
| `skeleton.json` | ✅ 存在 | 必需 | ✅ |
| `dataset.json` | ✅ 存在 | 必需 | ✅ |

源码依据：

- `data/runtime/unreal_dataset.py:215` 校验 `("skeleton.json", "stats/mean.npy", "stats/std.npy")` 三个文件，任一缺失即拒绝训练签名。
- `data/runtime/unreal_dataset.py:221-223` 校验 `(schema_version, coordinate_system)` 必须是 `(1, "unreal_component_cm_xyzw")` 或 `(2, "unreal_root_relative_cm_xyzw")`。

训练入口盘点：

| 脚本 | 数据源 | 能否直接吃我们的数据 |
| --- | --- | --- |
| `train_unreal.py` | UE Schema 数据集 | ❌ |
| `train_vqvae.py` | `SyntheticMotionDataset`（随机合成） | ❌ 不用真实数据 |
| `train_pose.py` / `train_root.py` | UE Schema | ❌ |
| `train_conditioned_pose.py` | 条件动作契约 | ⚠️ 待调研 |
| SOMA 专用入口 | — | ❌ 不存在 |

**结论：必须先补数据适配层，否则任何训练优化都是空谈。**

## 2. 五处性能损耗

### 2.1 P1：`num_workers=0`（最致命）

```
training/pretrain/train_unreal.py:226
  loader = DataLoader(Subset(dataset, indices), batch_sampler=sampler,
                      num_workers=0, collate_fn=collate_native)
training/pretrain/train_unreal.py:228
  loader = DataLoader(Subset(dataset, indices), batch_size=args.batch_size,
                      sampler=sampler, num_workers=0, collate_fn=collate_native)
training/pretrain/train_unreal.py:239
  loader = DataLoader(Subset(dataset, indices), batch_size=args.batch_size,
                      shuffle=True, num_workers=0, collate_fn=collate_fn)
```

三处 DataLoader 全部 `num_workers=0`，数据加载完全串行在主进程。

后果：GPU 每算完一步就停下来等 CPU 读盘。实测 4070 上 Encoder 单步仅 22.8 ms，而从 NVMe 读 4.25 MB NPZ 需要 10-30 ms——**GPU 有接近一半时间在空转等数据**。

预期收益：**2-4×**（取决于 CPU 与 I/O 带宽）。

### 2.2 P2：`precision=32`（FP32 未用 Tensor Core）

```
training/configs/pretrain/motionweaver_vqvae.yaml:76
  precision: 32

training/pretrain/train_unreal.py:256
  parser.add_argument("--precision", choices=["32", "bf16-mixed", "16-mixed"], default="32")
```

`train_unreal.py:215-216` 的 Trainer 直接取 `args.precision`，默认即 `"32"`。

4070 支持 bf16（已实测 `torch.cuda.is_bf16_supported() == True`），用 FP32 等于放弃 Tensor Core。

**注意**：显存不是瓶颈（Encoder 峰值仅 0.42 GB / 8 GB），所以切 bf16 的主要收益是算力而非显存。切之前应跑 loss 曲线对照 FP32，确认收敛行为一致。

预期收益：**1.5-2×**。

### 2.3 P3：每次 `__getitem__` 都整条重读（95% 数据被浪费）

```
data/runtime/unreal_dataset.py:382-385
  def __getitem__(self, index):
      item = self.manifest["clips"][index]
      return {"keyid": index,
              "motion": torch.from_numpy(np.load(self.files[index], allow_pickle=False).copy()),
              ...}
```

实测（`clips/fps120` 随机 50 条）：

| 指标 | 值 |
| --- | ---: |
| 单个 clip 平均大小 | **4.25 MB** |
| 平均帧数 | **905 帧** |
| 训练窗口实际需要 | **48 帧 = 0.23 MB** |
| 单帧体积 | 约 4.6 KB |
| **被浪费的比例** | **95%** |

关键：一条 clip 会被复用约 18 次（905 ÷ 48），所以「首次加载的浪费」被摊薄。真正的问题是**每次都重新 `np.load`，没有跨 epoch 缓存**——同一份 4.25 MB 文件在一次训练里会被反复读几十遍。

预期收益：加 LRU 缓存后 **2-3×**；若改成按窗口切片存储，理论可到 18×（但受 I/O 上限约束，实测更可能是 2-3×）。

### 2.4 P4：未启用 `torch.compile`

配置与脚本均未见 `compile=True`。PyTorch 2.14 已稳定支持，能融合 Conv1d + Resnet 的算子。

预期收益：**1.2-1.5×**。

### 2.5 P5：`channels_last` 未设置

Conv1d 场景下收益有限，且需要验证数值正确性。优先级最低。

预期收益：**1.05-1.1×**。

## 3. 实测基准数据

在本机 4070 上用真实 `Encoder` 做的 forward+backward 实测：

| 模型 | 参数 | 窗口 | batch | 单步耗时 | 显存峰值 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `tok_A120` (down_t=4) | 21.8 M | 48 帧 | 8 | **22.8 ms** | 0.42 GB |
| `tok_A120` (down_t=4) | 21.8 M | 48 帧 | 16 | 23.5 ms | 0.41 GB |
| `tok_A120` (down_t=4) | 21.8 M | 48 帧 | 32 | 22.7 ms | 0.42 GB |
| `tok_BC120` (down_t=3) | 16.5 M | 72 帧 | 8 | **13.6 ms** | 0.39 GB |
| `tok_BC120` (down_t=3) | 16.5 M | 72 帧 | 16 | 15.8 ms | 0.32 GB |
| `tok_BC120` (down_t=3) | 16.5 M | 72 帧 | 32 | 19.5 ms | 0.32 GB |

观察：

1. `tok_A120` 的 batch 8/16/32 单步几乎不变（22.7-23.5 ms）——说明**当前是 kernel launch 开销主导，不是算力主导**，加大 batch 是免费的。
2. 显存占用 0.42 GB，相对 8 GB 有近 20 倍余量。

## 4. 完整模型的耗时推算

完整 VQ-VAE 含 encoder + quantizer + decoder，估算约为 encoder 的 2-2.5 倍：

| 项 | 估算 |
| --- | ---: |
| 完整模型单步 | 22.8 × 2.5 ≈ **57 ms** |
| 20,000 步（单个 tokenizer） | **约 19 分钟** |
| 4 个 tokenizer 合计 | **约 1.3 小时** |
| 100,000 步 × 4 个 | **约 6.3 小时** |

优化后（num_workers + bf16 + 缓存）：

| 项 | 优化后 |
| --- | ---: |
| 单步 | 约 **20-25 ms** |
| 4 个 tokenizer × 20k 步 | **约 30 分钟** |
| **综合提速** | **约 2.5-3×** |

## 5. 与历史配置的对比

历史 A800 训练（`docs/experiments/assets/2026-09-28-motionweaver-a800-actor-root/`）用的是：

- `max_steps: 20000`（配置模板写 2,000,001，实际只跑 20k）
- 8 卡 × 4 节点 = 32 卡 DDP
- 结果：Token CE 从 2.3247 降到 1.4401 nats（20k 步）

**注意**：`train_unreal.py:113` 和 `:215` 会把 `devices` 强制设为 1，所以配置里的 `devices: 8` / `num_nodes: 4` 对本地运行无效。这是好事——不会误启动多卡。

## 6. 建议的执行顺序

| 优先级 | 动作 | 理由 |
| --- | --- | --- |
| **P0** | 补 SOMA 数据适配层 | 不补就跑不了，其他优化无意义 |
| **P1** | `num_workers=4~8` | 收益最大（2-4×），改动最小 |
| **P2** | `bf16-mixed` | 1.5-2×，需 loss 曲线对照 |
| **P3** | clip 级 LRU 缓存 | 2-3×，改动中等 |
| P4 | `torch.compile` | 1.2-1.5×，可能有兼容问题 |
| P5 | `channels_last` | 收益小，最后再考虑 |

**关键建议**：P1 和 P2 应在写适配层时**一次性配好**，不要先写一个 `FP32` + `num_workers=0` 的版本再回来改——那样会浪费两轮调试。

## 7. 必须坚持的约束

1. **不损失训练效果**。bf16 切换前必须用同一份数据跑 FP32 vs bf16 的 loss 曲线对照；若收敛有偏差则放弃 bf16。
2. **`--max_steps` 必须显式传**。配置模板默认 `2,000,001`，本地不传会跑到天荒地老。
3. **`--result_dir` 指向 D 盘 NVMe**。checkpoint 每个几百 MB，不要写 E 盘。
4. **显存不是瓶颈**（0.42 GB / 8 GB），不要为省显存牺牲 batch size。

## 8. 未验证的部分

以下是**推断而非实测**，需在适配层完成后验证：

- 完整 VQ-VAE（含 decoder）的单步耗时倍数（估 2-2.5×）
- `num_workers>0` 在本机 NVMe 上的真实收益（估 2-4×）
- LRU 缓存的有效性取决于数据重复访问模式（估 2-3×）
- bf16 对收敛的影响（未知）

本机小文件 I/O 的历史实测延迟约 0.058 ms，属延迟受限型，因此 `num_workers` 提升到 4-8 后边际收益会快速衰减，不建议设到 16 以上。
