# SOMA tokenizer 码本初始化 A/B 对照

## 结论

**保持 `kmeans_init: false`（随机初始化）作为生产配置。**

两个变体各训 3 个 epoch（42,791 步、batch size 4、FP32、fps120 组 57,058 条训练样本）。
末段 10%（4,279 步）的中位数比较：总 loss 0.4026 对 0.5165，随机初始化低 28.3%。
k-means 初始化的 commit 损失中位数低 36.9%，但那是**码本未被用起来**的副产物而非优势
（见下文「一个只看数字会读错的地方」）。两臂分布大幅重叠，所以这是
「统计上成立、幅度中等」的结论，不是压倒性优势。

## 两个变体

| 项 | A | B |
|---|---|---|
| `kmeans_init` | false（随机初始化） | true（首批数据 k-means 初始化） |
| 训练方式 | 先 20,000 步，再续训到 42,792 步 | 从头训 42,792 步 |
| 数据 | `full-v1-f32`，fps120，57,058 条训练样本 | 同左 |
| 窗口 / batch | 48 帧 / 4 | 同左 |
| 精度 | FP32 | 同左 |
| 产物 | `training_runs/tok_A120_20k` | `training_runs/tok_B120_3ep` |

配置通过 `--kmeans_init 0/1` 覆盖，不复制 YAML，见提交 `24a8017`。

## 末段 10%（step ≥ 38,513，4,279 步）

同时给均值与中位数：原始逐步数据含 40+ 的尖峰，只看均值会被离群点抬高。

| 指标 | 统计量 | A | B | 差异 |
|---|---|---|---|---|
| **总 loss** | 均值 | **0.6838** | 0.8694 | A 低 27.1% |
| | **中位数** | **0.4026** | 0.5165 | **A 低 28.3%** |
| | p90 | 1.5749 | 2.0279 | A 低 28.8% |
| 重建损失 | 均值 | 0.1811 | 0.1815 | 持平（+0.2%） |
| | 中位数 | 0.1632 | 0.1656 | A 低 1.5% |
| perplexity | 均值 | **5.2925** | 5.1851 | A 高 2.0% |
| commit 损失 | 均值 | 0.5844 | **0.5519** | B 低 5.6% |
| | **中位数** | 0.3559 | **0.2247** | **B 低 36.9%** |

## 一个只看数字会读错的地方

B 的 commit 损失明显更低，看起来更好。但 commit 损失衡量的是
「编码器输出与所选码字的距离」——它低也可能意味着**码本根本没被用起来**。

perplexity 曲线证实了后者：A 在第 10k 步就把码本用到 4.5，
B 一直压在 2 附近、**直到 25k 步才开始上升**。所以 B 早期那个
「极低的 commit 损失」不是学得好，而是量化器近似退化的副产物。

这条对任何「以损失更低为唯一判据」的实验都成立：**必须给每个损失项
配一个能证明对应模块真的在工作的量**，否则低损失可能是模块停摆的症状。

早期版本的本报告写成「B 早期快、后来被反超」，方向说反了：
B 不是学得快，是码本启动得慢。commit 低恰恰是码本没启动时的必然现象。

## 按步对齐（说明 B 的码本启动延迟）

| 指标 | 区间 | A | B | 更优 |
|---|---|---|---|---|
| commit 损失 | 0–5k | 0.1369 | **0.0943** | B |
| commit 损失 | @10k | 0.2672 | **0.0023** | B（但见上一节） |
| 重建损失 | @10k | 0.6970 | **0.2562** | B |
| 总 loss | @20k | **0.4890** | 1.1378 | A |

## 一个必须说明的数据陷阱

A 在 @10k 的总 loss 是 2.4223，看起来像训练崩了一次。实际是**续训边界伪影**：
A 由两个 run 拼接（前 20,000 步 + 续训），在 20,000 步附近切换 version 时，
优化器状态与学习率调度重新初始化，产生瞬时扰动。判断 A 与 B 的优劣必须用同期基线，
不能用这个尖峰当证据。

## 码本容量的正确解读

perplexity 约 5.1–5.3 曾被误判为「码本塌缩」。实际不是：

- 配置 `nb_code: 100000000`、`num_heads: 8`，代码算出 `nb_code_per_head = 10`；
- `calculate_per_head_perplexity: true`，日志里的 perplexity 是**单 head** 的；
- 因此 perplexity 上限是 10，实测 5.3 是 **53% 单头利用率**，不是塌缩；
- 真正的表达力来自 8 个 head 的**乘积量化**：`from_mh_indices_to_overall_indices`
  用 `10^i` 加权求和，组合数为 10^8。

历史 2026-09-28 的 A800 配置同样是 `nb_code=100000000` + `num_heads=8`，
因此这是原版设计而非本项目配错。要提升表达力应调 `nb_code_per_head`，
但**在拿到重建误差的厘米级数值之前不该动它**——码字越多，每个码字被采样越少，
EMA 更新越慢，越容易出现死码。目前的重建损失 0.1811 是归一化特征空间里的
`SmoothL1Loss`，没有物理单位，无法据此判断「够不够好」。

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

实测速度约 22–25 it/s（FP32，num_workers=4 + persistent_workers），
单臂 42,792 步约 30 分钟。

## 图表

- `D:\MotionWeaverData\reports\tokenizer_ab_smoothed.png` —— 四个指标的分桶中位数曲线
- `D:\MotionWeaverData\reports\tokenizer_ab_tail_dist.png` —— 末段分布的箱线图
- `D:\MotionWeaverData\reports\tokenizer_ab_curves.png` —— 未平滑的原始逐步曲线（核查用）

## 环境

RTX 4070 Laptop 8GB、torch 2.14.0+cu130、32 逻辑核、D 盘 NVMe。

