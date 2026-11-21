# 官方 MotionBricks 与 MotionWeaver 路线对照（封板）

> 封板日期：2026-11-21。状态：**对照冻结，后续研究入口**。
> 本文只归档已发生的事实、当前权重的实际画面与后续待验证问题，不代表已决定重训。
> 本次没有启动训练、修改模型或产生新权重。
>
> 官方配置取自随仓 G1 权重的
> [pose](../model-weight/base/motionbricks/motionbricks_pose/version_1/hparams.yaml)、
> [root](../model-weight/base/motionbricks/motionbricks_root/version_1/hparams.yaml)、
> [vqvae](../model-weight/base/motionbricks/motionbricks_vqvae/version_1/hparams.yaml)。
> 我们正在看的权重是本机 `runs/refpose_v2_30ep/best.pt`（3,087,038 参数，第 29 轮）。
> 网络定义在 [ReferenceGuidedPose](../model/MotionWeaver/refpose/model.py)。
> 官方三件套源码仍在 `model/MotionWeaver/`，这条主线不用它们。
> v3 封盘（验证 3.654 cm）不在本机，不能和这包 v2（验证 3.91 cm）当成同一次结果。

## 一页结论

1. 预期路线应是「官方 MotionBricks 为主干，我们增加数据、骨架、接触和 UE 优化」；
   实际当前 RefPose 是另建的 3.09M 短窗直接回归网络，不是官方模型的减量版。
2. 官方三件套从未在我们的 78 骨 / 120 fps 数据上完整跑通。
   UE 实验只跑了官方 VQ 加缩小的 Pose；SOMA 实验在 VQ 失败后切换到直接回归。
3. 当前画面问题不能只归因于数据不足：本机 RefPose 使用 SOMA 全库，
   每窗喂真实历史时尚可，吃自己的历史时持续漂移，说明开环指标不能代表长时闭环。
4. 官方公开 **SOMA 人体与 G1 两套权重**及 ONNX；本地已有 G1 三件套实体文件。
   它们不能直接变成 78 骨 / 120 fps 权重，但提供了应当复现和对照的官方基线。
5. 数据、动作表示、上游源码、可视化与部分导出工具仍然有效；
   需要重新接续的是官方模型训练路线，不是把整个仓库推倒。

## 官方权重与基线

| 项目 | 已核实事实 |
| --- | --- |
| 官方公开模型 | `MotionBricks-SOMA-RP-v1` 与 `MotionBricks-G1-RP-v1`，均为 30 fps |
| 权重形式 | Hugging Face 上为 SafeTensors；官方仓库同时提供 SOMA / G1 ONNX |
| 官方生成方式 | 4 个最近姿态 + 1–4 个目标姿态；Root 预测路径和时长，Pose 生成 token，VQ 解码 |
| 连续动作 | 每次生成约 0.6–1.9 秒；下一段使用实际播放记录的最后 4 个姿态继续生成 |
| 本机权重 | G1 VQ、Pose、Root 三份 `model-step=2000000.ckpt` 均为实体文件，不是 LFS 指针 |
| 与本项目骨架的关系 | 官方 SOMA / G1 均不是本项目的 78 骨 / 120 fps；不能直接加载为本项目权重 |
| 许可 | 代码 Apache-2.0；模型 NVIDIA Open Model Agreement；示例动作另受评估数据许可约束 |

官方入口：[仓库](https://github.com/NVlabs/motionbricks)、
[权重](https://huggingface.co/nvidia/MotionBricks)、
[运行时说明](https://github.com/NVlabs/motionbricks/blob/main/docs/runtime_api.md)。
随仓 G1 配置显示 VQ、Pose、Root 各训练 2,000,001 步，使用多卡；
这些文件没有给出完整墙钟时间，不能只凭步数把训练小时数写成事实。

## 路线总表

这里按模型血缘统计，不把数据构建、重定向、mmap、ONNX 导出或 UE 接线算成新的模型路线。
官方随仓配置与我们的数据、骨架、评测集不同，表中的误差不能跨行直接判定谁优于谁。

| 路线 | 与官方的关系 | 数据 | 骨架 / 帧率 | 模型与条件 | 训练规模 | 实测结果与结局 |
| --- | --- | --- | --- | --- | --- | --- |
| 官方 MotionBricks G1 | 官方原始三件套与官方权重 | 官方训练数据重定向到 G1 | 34 关节 / 30 fps | VQ-VAE + 16 层 Pose + 独立 Root；历史、目标帧、可选文本 | VQ、Pose、Root 各 2,000,001 步；VQ/Pose 32 卡，Root 16 卡 | 官方 G1 演示保留；没有在我们的 78 骨测试集上跑过 |
| UE VQ-VAE | 沿用官方 VQ 架构，换骨架后自训 | 旧 UE prepared | 79 骨 / 30 fps | 动作重建；训练配置仍含 8 头、256 维码向量 | 已有 `final.ckpt`；当前记录未统一核实总步数 | 给后续 Pose Token 实验提供冻结分词器；不代表玩家闭环通过 |
| UE 直接回归 RefPose | 另起小模型，不走官方三件套 | 旧 UE 条件数据，约 1.3k train clips、26,861 train 窗口 | 79 骨 / 30 fps | GRU + 3 层 Transformer；历史、外部 Root、硬参考 | 多轮小数据训练与接触消融 | 成为轻量对照；数据少、接触和生成历史回灌未通过 |
| UE Pose Token / Actor Root | 官方 VQ + 改小的官方 Pose 结构；没有训练 Root | 1,722 条，train 1,355；30 fps | 79 骨 / 30 fps | 冻结 VQ；6 层、宽 256、8 头 Pose，8,278,544 可训练参数；前 4 帧 + Actor Root | A800 单卡 20,000 步，batch 8 | 验证 Token CE 1.86195→1.45361；Walk/Run/Crouch RMSE 6.03/12.00/8.25 cm；CMC 蹲伏失败，未采用 |
| SOMA VQ-VAE | 沿用官方 VQ 思路，适配全库后自训 | SOMA 全库约 71k 条 | 78 骨 / 120 fps | 动作重建，多头离散码本 | 100,000 步 | 关节 RMSE 约 84–86 cm；20k 步后码本 perplexity 塌到 1.000；路线停止 |
| SOMA RefPose 小数据版 | 另起直接回归模型 | 约 2k 条子集 | 78 骨 / 120 fps | 3.09M 直接回归；24 帧历史 + 未来 24 帧 oracle Root + 硬参考 | 30 epoch | 旧记录验证 4.28 cm、无参考 6.74 cm、脚滑 17–22×；与后续全库结果不同口径 |
| SOMA RefPose v3 封盘 | 另起直接回归模型 | 71,127 条；1,988,285 train 窗口 | 78 骨 / 120 fps | 同上；0–2 帧硬参考，无文本、无码本、无 Root 生成 | 20 epoch，batch 256，8.7 小时 | test 四档 5.28/3.22/3.22/2.17 cm；脚滑 6.9–8.7×；权重不在本机 |
| SOMA RefPose v2 全量 30ep | 同一直接回归路线 | 71,128 条；1,992,148 train 窗口 | 78 骨 / 120 fps | 同上 | 233,430 步，batch 256；后 10 epoch 续训 39.9 分钟 | test 四档 5.40/3.39/2.94/2.38 cm；本机长视频开环可看，闭环持续漂移 |

这些失败只否定当时的具体实现与训练结果，不证明官方 VQ / Pose / Root 架构本身不适用于 78 骨。

## 哪些真的继承了官方

| 官方能力 | 我们是否在我们的骨架上跑通过 | 实际情况 |
| --- | --- | --- |
| VQ-VAE | 跑过，但未达到可用重建质量 | UE 版用于后续实验；SOMA 版码本塌缩 |
| 完整 16 层 Pose | 没有 | UE 实验缩成 6 层、宽 256；SOMA 没有继续训练官方 Pose |
| 独立 Root 模型 | 没有 | 当前 refpose 使用外部 oracle Root；`RootPlanHead` 不在本机这包权重中 |
| Pose → VQ 解码长时闭环 | 没有 | UE Pose Token 质量未通过；SOMA 在 VQ 阶段停止 |
| 文本、风格与智能基元 | 没有接到当前主线 | 只保留上游源码与 G1 演示 |
| 78 骨 SOMA 官方三件套完整复现 | 没有 | 实际主线切成了 0.2 秒直接回归 |

因此，准确的总结不是“官方模型微调到一半”，而是：

1. 数据、动作表示、上游源码和部分 UE/导出工具继承下来了。
2. UE 上试过官方 VQ + 改小 Pose；SOMA 上只把官方 VQ 路线跑到失败结论。
3. 当前主线 RefPose 是另一套模型，不是官方 MotionBricks 的减量版。
4. 尚未完成的关键工作仍是：稳定 VQ、官方 Pose/Root、长时生成与闭环。

| 方面 | 官方 MotionBricks | 我们的 refpose |
| --- | --- | --- |
| 结构 | 三件套：VQ-VAE、姿态 Transformer、独立根模型 | 一个网络直接回归姿态 |
| 姿态网络 | 16 层、1024 维、16 头；掩码 token 生成（掩码比例 0.8） | GRU 读 24 帧历史，再加 3 层、4 头、宽 256 的 Transformer |
| 参数量 | 姿态网络远大于我们；另加分词器和根模型 | 3,087,038 |
| 分词器 | 多头 EMA 码本，8 头、码向量 256 维，时间下采样 4 倍 | 没有。78 骨上自训的 VQ 码本塌到 perplexity 1，关节误差停在 84 cm，已停 |
| 根 | 另一个网络预测局部根、全局根和这段有多长 | 未来 24 帧根由外部逐帧喂入。这次视频用的是原动作。代码里的 `RootPlanHead` 不在这包权重里 |
| 一次生成 | 6–16 个 token，大约 0.6–1.9 秒 | 固定 24 帧。120 fps 下是 0.2 秒，长动作要一窗接一窗 |
| 骨架 / 帧率 | 随仓权重是 G1 34 关节；官方人体线大约 30 骨、30 fps | 78 骨，含手指，120 fps |
| 参考帧 | 最多 4 个起点、4 个终点、8 个中间帧，作为 token 条件 | 0–2 帧硬参考，选中帧在输出端被盖住。这次播放掩码全是 0 |
| 文本 / 风格 | 姿态模型可吃 4096 维文本（约 20% 批次）；演示能切风格和物体交互 | 没有 |
| 接触 | 训练有脚滑损失；表示层留了足部接触特征。这套 G1 配置实际用的是髋高，不是接触通道 | 没有接触输出头。接触脚速惩罚只在另一条训练里可选 |
| 训练规模 | 姿态模型 2,000,001 步，多机多卡 | v2 为 30 epoch，单卡 |

## 多出来的

- 78 骨、120 fps，手指在骨架里。
- 未来根可以换成游戏给的轨迹；这包评测和视频仍用源动作的根。
- 参考帧是硬约束，不是软条件。
- 网络小，直接出位置和 6D 旋转，方便导出。

## 少掉的

- 码本、大姿态 Transformer、独立根模型。
- 一次生成零点几秒到两秒；我们只有 0.2 秒，闭环把上一窗的输出接回去就会漂。
- 文本、风格和物体交互那套控制。
- 接触通道。

官方是「在码本里补一块比较长的动作，根也自己生成」。我们是「0.2 秒一块，连续姿态直接回归，根靠外部给」。

## 本机权重的实际画面

2026-11-21 使用 `refpose_v2_30ep/best.pt`、无参考帧、源动作未来 Root，
对走、跑、蹲分别渲染开环与闭环。两种模式只改变下一窗历史来源：

| 模式 | 下一窗历史 | 画面结论 |
| --- | --- | --- |
| 开环 | 源动作真实历史 | 人形和动作大体保留；这是每窗重置误差的诊断，不是连续自回归能力 |
| 闭环 | 模型自己的上一窗输出 | 约 1 秒后开始明显漂移，后续走姿和跑姿崩坏 |

视频采用稳定斜前机位、半速播放，画面 `t=` 为动作时间。左蓝为源动作，右橙为模型：

| 动作 | 开环 | 闭环 |
| --- | --- | --- |
| 走 | `.build/refpose_playback/long/walk_open.mp4` | `.build/refpose_playback/long/walk_closed.mp4` |
| 跑 | `.build/refpose_playback/long/run_open.mp4` | `.build/refpose_playback/long/run_closed.mp4` |
| 蹲 | `.build/refpose_playback/long/crouch_open.mp4` | `.build/refpose_playback/long/crouch_closed.mp4` |

这些视频是本机证据，不入 Git。该观察不能外推为 v3 封盘权重或 UE 蒙皮的结果。

## 数据是不是问题

| 路线 | 数据范围 | 过拟合与分布判断 |
| --- | --- | --- |
| 旧 UE 小数据实验 | 约千级动作，场景和状态覆盖有限 | 泛化风险高；不能代表 SOMA 全库路线 |
| 当前 SOMA RefPose | 约 71k clips，train 约 199 万重叠窗口 | 不是「只跑 UE 数据」；窗口多不等于独立动作多，开闭环分布也不一致 |

后续若继续，应以 SOMA 为通用预训练来源，UE 重定向数据作补充或微调并混入 SOMA 回放；
按演员、动作家族和原始 take 隔离测试，不能把重叠窗口数量当作泛化证据。

## 下次恢复时的研究顺序

1. 先运行官方 SOMA 权重，保存官方长时连续生成基线，不改我们的骨架。
2. 用同一批源动作比较官方输出、当前 RefPose 开环与闭环，固定镜头和评价协议。
3. 单独验收 78 骨 VQ：码本使用率、重建画面、骨长与接触都必须通过，不能只看归一化损失。
4. VQ 达标后恢复官方 Pose / Root，不缩小主干来冒充完整复现。
5. 最后增加我们的 120 fps、UE 控制、接触、ONNX/NNE 与性能优化。

这是后续建议，不是已执行计划；新的实验应另建记录，不改写本文的历史结果。

## 证据入口

- 官方 G1 配置与本地权重：[model-weight/base/motionbricks](../model-weight/base/motionbricks)
- 当前直接回归网络：[ReferenceGuidedPose](../model/MotionWeaver/refpose/model.py)
- v3 历史封盘记录：[refpose-v3-training-evaluation.md](refpose-v3-training-evaluation.md)
- 本机 v2 30ep 训练记录：[2026-11-14-refpose-v2-30ep.md](experiments/2026-11-14-refpose-v2-30ep.md)
- UE Pose Token 20k 记录：[2026-09-28-motionweaver-a800-actor-root.md](experiments/2026-09-28-motionweaver-a800-actor-root.md)
- UE 实际生成与视觉限制：[2026-11-18-refpose-pose-group-playback.md](experiments/2026-11-18-refpose-pose-group-playback.md)
- 生态调研：[refpose-ecosystem-landscape.md](refpose-ecosystem-landscape.md)
