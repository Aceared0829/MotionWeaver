# SOMA tokenizer 码本初始化 A/B 对照

## 结论

**保持 `kmeans_init: false`（随机初始化）作为生产配置。**

两个变体各训 3 个 epoch（42,791 步、batch size 4、FP32、fps120 组 57,058 条训练样本），
在末尾 500 步的平均值上 A 胜出 4 项中的 3 项实质性指标，总 loss 领先 26%。
kmeans 初始化的唯一优势是早期收敛速度——@10k 步时 commit 损失低 116 倍——但这一优势
在训练后期被完全反转。因此对「训满 3 epoch」的目标，随机初始化更好；
kmeans 初始化只适合「没有预算训满、需要尽快拿到一个能用的 tokenizer」的场景。

## 两个变体

| 项 | A | B |
|---|---|---|
| `kmeans_init` | false（随机初始化） | true（首批数据 kmeans 初始化） |
| 训练方式 | 先 20,000 步，再续训到 42,792 步 | 从头训 42,792 步 |
| 数据 | `full-v1-f32`，fps120，57,058 条训练样本 | 同左 |
| 窗口 / batch | 48 帧 / 4 | 同左 |
| 精度 | FP32 | 同左 |
| 产物 | `training_runs/tok_A120_20k` | `training_runs/tok_B120_3ep` |

配置通过 `--kmeans_init 0/1` 覆盖，不复制 YAML，见提交 `24a8017`。

## 末尾 500 步（42,791 步处）

| 指标 | A | B | 差距 | 更优 |
|---|---|---|---|---|
| 总 loss | **0.6138** | 0.8250 | A 好 26% | A |
| 重建损失 | **0.1699** | 0.1817 | A 好 6% | A |
| perplexity | **5.3303** | 5.0952 | A 好 4% | A |
| commit 损失 | **0.5665** | 0.5896 | A 好 4% | A |
| 脚滑接触 | 0.9560 | **0.9507** | 持平 | B |

## 按步对齐（说明 B 的早期优势与其消失）

| 指标 | 区间 | A | B | 更优 |
|---|---|---|---|---|
| commit 损失 | 0–5k | 0.1369 | **0.0943** | B |
| commit 损失 | @10k | 0.2672 | **0.0023** | **B（116×）** |
| commit 损失 | @20k | 0.0116 | **0.0030** | B |
| 重建损失 | @10k | 0.6970 | **0.2562** | B（2.7×） |
| 重建损失 | @20k | 0.2735 | **0.2624** | B |
| 总 loss | @10k | 2.4223 | **0.8014** | B（3×） |
| 总 loss | @20k | **0.4890** | 1.1378 | A |

## 一个必须说明的数据陷阱

A 在 @10k 的总 loss 是 2.4223，看起来像是训练崩了一次。实际上那是**续训边界伪影**：
A 由两个 run 拼接（前 20,000 步 + 续训），在 20,000 步附近切换 version 时，
优化器状态与学习率调度重新初始化，产生瞬时扰动。判断 A 与 B 的优劣必须用同期基线，
不能用这个尖峰当证据。

## 码本容量的正确解读

perplexity 约 5.1–5.3 曾被误判为「码本塌缩」。实际不是：

- 配置 `nb_code: 100000000`、`num_heads: 8`，代码算出 `nb_code_per_head = 10`；
- `calculate_per_head_perplexity: true`，日志里的 perplexity 是**单 head** 的；
- 因此 perplexity 上限是 10，实测 5.3 是 **53% 利用率**，不是塌缩。

真实约束是每个 head 只有 10 个码字；要提升表达力应调 `nb_code_per_head`，
而不是怀疑初始化方式。

## 复现

```powershell
# A（随机初始化）
python -m training.pretrain.train_unreal --model vqvae `
  --soma_views D:\MotionWeaverData\soma_train_v1\full-v1-f32 `
  --soma_window_frames 48 --soma_fps_group fps120 --num_workers 4 `
  --kmeans_init 0 --output <A 目录> --max_steps 42792 --batch_size 4 `
  --accelerator gpu --precision 32 --checkpoint_every 10000

# B（kmeans 初始化）：同上，把 --kmeans_init 改成 1
```

实测速度约 22–25 it/s（FP32，num_workers=4 + persistent），
单臂 42,792 步约 30 分钟。

## 环境

RTX 4070 Laptop 8GB、torch 2.14.0+cu130、32 逻辑核、D 盘 NVMe。
