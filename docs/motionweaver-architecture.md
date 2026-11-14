# MotionWeaver：原版继承与 UE 玩家模型路线

> **2026-11-14 注记：** 本文保留 2026-09 的 MotionWeaver Pose Token / Actor Root 路线记录。
> 当前主线已转为 refpose v3 参考引导姿态补全，正式结果见
> [refpose v3 训练与评测](refpose-v3-training-evaluation.md)；UE 侧现状以
> [Unreal 集成](../unreal-script/README.md) 为准。

状态：2026-09-28 已实现独立 Actor Root 条件的 Pose Token 训练与本地推理入口；A800 上的 MotionWeaver Pose **从零完成 20000 步**，本地同轨与录制 CMC 评估已完成。固定 validation Token CE 明显改善，但 Run/Crouch 姿态、脚滑和 CMC 站→蹲测试未过质量门槛。E 盘现可读取；79 骨 UE VQ-VAE 检查点已核对。UE NNE/AnimGraph 尚未接入 MotionWeaver。工作树 `D:/AILocomotonSystem/GR00T-WholeBodyControl-p0-review`，代码基点 `e07aad2`，保留前序未提交实验。原版 `model/MotionWeaver/` 网络与 G1 演示仍保留；本轮未改变现有玩家试玩的 UE 推理行为。训练和验证详情见[已完成的实验记录](experiments/2026-09-28-motionweaver-a800-actor-root.md)。

## 名称与边界

- **AIAnimationSystem**：项目名称。
- **MotionBricks**：上游模型架构、原始代码与 G1 参考演示的来源名称。继承的网络统一维护在 `model/MotionWeaver/`，训练封装归 `training/models/`，参考推理归 `inference/runtime/backbone/`；上游权重继续使用原目录，保留来源和许可归属。
- **MotionWeaver**：基于 MotionBricks 架构适配 UE 玩家控制的模型。`model/MotionWeaver/` 保存 tokenizer、backbone、动作表示与几何源码；已实现的 Lightning 训练适配位于 `training/models/backbone/`，训练与推理入口分别位于 `training/pretrain/` 和 `inference/runtime/`。共用上游网络时直接引用同一份实现，并记录差异与来源。旧 `motionbricks.*` Python 名称由安装映射保留，用于现有配置和检查点兼容。
- **ReferenceGuidedPose**：现有 UE 试玩用的约 310 万参数直接回归实验模型，定义在 [`training/pretrain/train_conditioned_pose.py`](../training/pretrain/train_conditioned_pose.py)。继续作为可复现的轻量对照；其现有 ONNX、日志和指标不得改标签冒充 MotionWeaver 成绩。

## 本地代码实际差别

| 层 | MotionBricks 原版 / 当前保留代码 | 当前 UE 试玩 `ReferenceGuidedPose` | MotionWeaver 目标 |
| --- | --- | --- | --- |
| 数据 | `data/runtime/synthetic_dataset.py` 为原版训练入口；UE 数据另有 `unreal_dataset.py` | UE 独立 Root 与 79 骨姿态；`conditioned_motion.py` 制作 24+24 帧窗口，冻结契约和划分 | 已将 UE `raw_*.npz` 的独立 Actor Root 逐帧制成 sidecar，并与姿态窗口同步；训练不把骨盆相对 Root 当 CMC 世界轨迹，也不制造玩家按键真值 |
| 表示 | `model/MotionWeaver/tokenizer/` 的多头离散码本与条件解码 | 每骨位置和 6D 旋转直接回归，没有动作 Token | 已核对 UE 79 骨 VQ-VAE 检查点与数据签名；Pose Token 网络复用原版结构，正式 20000 步权重已保存在 `output/motionweaver_actor_a800_20k_20260928/` |
| 生成 | `motion_backbone/` 包含 Root 与 Pose 模块；原版推理先 Root 后 Token/解码 | GRU 历史编码 + 3 层 Transformer，给定外部 Root 直接输出 79 骨 | 已实现 Actor Root 轨迹条件 → Pose Token → VQ 解码的本地入口；在线输入仅给前 4 帧历史姿态。模型生成 Actor Root、在线样例关键帧仍属后续能力 |
| 世界位移 | 原版 Root 模块可生成并细化 Root | 当前试玩由 CMC 决定 Actor，模型只给姿态；轨迹/坐标仍待对齐 | 运动来源可选 CMC、Mover、模型生成；UE 执行层处理碰撞及最终位移，实际 Root 回馈姿态生成 |
| 训练 | `training/pretrain/train_{vqvae,root,pose}.py`；UE 入口 `train_unreal.py` 可训练 VQ-VAE/Pose，pose-only 契约拒绝 Root 训练 | `train_conditioned_pose.py` 和后续短训脚本；来源未来 Root 配对监督 | `train_unreal.py --motionweaver_online` 已按 Actor Root sidecar 在 A800 从零训练 20000 步；固定 48 条 validation 的 Token CE 为 1.4536 nats。模型驱动 Root 需另立数据与训练契约 |
| 推理 | `inference/runtime/backbone/motion_inference.py` 使用 Root/Pose/VQ-VAE，G1 演示走 `inference/demo/` | `inference/runtime/conditioned_pose.py` 与 `output/playable_player_20260927/bundle/*.onnx`；UE NNE/AnimGraph 消费 | `inference/runtime/motionweaver.py` 已加载正式 Pose 权重做本地来源 Root 与录制 CMC Root 推理；脚滑与蹲伏响应未过验收，UE NNE 实时闭环尚未接入 |

源码依据：[原版 VQ-VAE](../model/MotionWeaver/tokenizer/neural_modules/vqvae.py)、[原版 Root](../model/MotionWeaver/backbone/neural_modules/root_backbone.py)、[原版 Pose](../model/MotionWeaver/backbone/neural_modules/pose_backbone.py)、[原版推理编排](../inference/runtime/backbone/motion_inference.py)、[UE 原版模型训练入口](../training/pretrain/train_unreal.py)、[直接回归模型](../training/pretrain/train_conditioned_pose.py)。

## 目标目录和职责

```text
model/
  MotionWeaver/
    tokenizer/               VQ-VAE 编码器、码本与条件解码器
    backbone/                Pose / Root 网络与采样
    representation/          动作表示、骨架与共享特征
    geometry/                几何计算
data/
  runtime/unreal_dataset.py   现有 UE 骨架/特征加载
  runtime/conditioned_motion.py 现有 Root/姿态窗口契约
  runtime/motionweaver_dataset.py UE 姿态与独立 Actor Root sidecar 对齐
  runtime/motionweaver_root.py Actor 世界轨迹转首帧局部 4D 条件
training/
  models/backbone/motionweaver_pose_model.py 在线前 4 帧姿态条件的 Pose Token 适配
  pretrain/train_unreal.py     UE VQ-VAE/Pose 入口；MotionWeaver Actor 条件训练已扩展
  pretrain/train_conditioned_pose.py 旧轻量对照，不作为 MotionWeaver 实现
inference/
  runtime/backbone/           原版推理编排，保留
  runtime/conditioned_pose.py 旧轻量对照
  runtime/motionweaver.py     Actor Root 条件推理、离线 Pose Root 诊断、检查点校验
unreal-script/
  AIAnimationMotionMatching/   本地未提交的玩家 CMC/AnimGraph 桥接，当前仍用旧 ONNX
.build/actor_root_sidecar_20260928/  与 UE 数据对应的训练 sidecar，非模型权重
```

底层网络统一维护在 `model/MotionWeaver/`；当前 MotionWeaver 的 Lightning Pose Token 包装位于 `training/models/`，数据转换位于 `data/`，训练器位于 `training/`，推理编排位于 `inference/`，UE 负责控制状态与蒙皮接线。新代码不复制第二份上游实现，修改时记录原模块及差异。

## 运动来源与唯一实际位移

MotionWeaver 的长期目标是三种运动来源共享同一套骨架、Pose Token 和解码器。模式由角色/动作的运动策略选择，不能在同一个更新步让多个来源同时改写 Actor。每步记录所选来源、提议 Root、UE 实际执行 Root 和姿态时间戳。

| 来源 | Root 与姿态生成 | UE 执行与回馈 |
| --- | --- | --- |
| CMC | CMC 的历史及可在线预测的短期轨迹约束 Pose Token/解码 | CMC 处理移动和碰撞；实际位移回馈下一窗口 |
| Mover | Mover 的历史及可在线预测的短期轨迹约束同一 Pose Token/解码 | Mover 处理移动和碰撞；实际位移回馈下一窗口 |
| 模型动画驱动 | 推理产生动画 Root motion 与配套 Pose Token/姿态；Root 分支可提出轨迹，但必须与解码动画选定同一条最终 Root | 通过选定的 UE 运动执行层应用并解决碰撞；以被接受的实际位移重规划、修正脚步和下一窗口 |

本地 `predict_with_actor_root` 接收独立 Actor 轨迹 `[B,T,7]`（Motion 坐标，米、Y 向上、xyzw 四元数），用首帧 Actor 局部的水平轨迹与朝向形成 Pose Token 条件，输出**Actor 相对的身体特征**。CMC 与 Mover 可使用同一输入契约，前提是它们实际提供在线可得的历史与未来计划；目前只是共享接口，并未完成两种 UE 运动组件的接线、NNE 导出或实时闭环验证。`predict_with_pose_root` 仅用于明确标注的离线诊断：其中 5 维 Root 是相对 Actor 的骨盆特征，不能拿 CMC/Mover 世界轨迹直接填入。模型驱动 Actor Root 尚未实现；现有 `pose_only_root_authoritative` 契约明确禁止据此训练 Root 分支。

## 实施顺序与准入

1. **保全原版与基线。** 固定原版源码、G1 演示及当前试玩 ONNX 的版本和清单。`ReferenceGuidedPose` 保留独立评估名。E 盘当前已可读取，UE 数据与 79 骨 VQ-VAE 检查点的训练签名、文件哈希已核对；G1 检查点在本地只是 Git LFS 指针，未用作 UE 权重。
2. **验证 UE VQ-VAE/Pose 已有基础。** UE VQ-VAE 已按数据契约核对；MotionWeaver Pose Token 的 A800 20000 步训练和本地固定 validation 已完成。Token CE 从 2k 的 1.862 降至 1.454 nats，但世界姿态和接触质量仍需改善。
3. **实现 MotionWeaver 给定 Actor Root 链路。** 使用原版 Tokenizer、Pose Token 网络和解码器。Actor Root sidecar、前 4 帧在线姿态条件、本地 Actor Root 推理入口与正式权重 Walk/Run/Crouch 离线推理已完成。下一步补在线可获得的 CMC/Mover 轨迹、Stance/Gait/RotationMode 输入及缺失掩码，并导出可复核的 UE 包。
4. **处理闭环一致性。** 核对 Actor、Mesh、骨架 Root 和模型坐标；统一 30 Hz 时间线与 4 帧重规划；检查生成历史和模式切换。每步只采用所选 UE 运动执行层的实际位移；模型输出姿态需在该 Root 上检验脚部接触。
5. **扩展模型动画驱动。** 在具备可训练的 Root 数据及合同后，令模型输出动画 Root motion 与配套姿态，明确 Root 分支和解码结果之间谁给出最终增量；经 UE 运动执行层应用碰撞和约束，再以实际位移闭环重规划。此项不能靠现有 pose-only 数据或简单启用 G1 Root 权重完成。
6. **同条件比较。** 相同 UE 骨架、冻结划分、控制轨迹、NNE/UE 环境下对比原 MM、旧直接回归模型与 MotionWeaver。分别报告方向与速度匹配、接触脚速、姿态误差、接缝、推理延迟和蒙皮视频；来源未来 Root 离线分数单列，并分别标明 CMC、Mover、模型驱动模式。

截至 2026-09-28，E 盘可读；真实 UE VQ-VAE 已通过签名和本地加载验证。MotionWeaver Pose 的 A800 20000 步训练已完成：同来源 Actor Root 的 Walk 姿态 RMSE 6.03 cm、Run 12.00 cm、Crouch 8.25 cm；Walk 来源接触脚速 0.031 m/s、生成 0.780 m/s。录制 CMC 站→蹲片段未让模型蹲下，因为蹲伏状态未输入。当前 UE 试玩仍使用旧 ONNX；MotionWeaver 的 UE NNE/蒙皮闭环、Mover 接线和模型驱动 Root 均未完成。详见[本轮实验](experiments/2026-09-28-motionweaver-a800-actor-root.md)。

继续优化 `ReferenceGuidedPose` 的训练属于**对照路线**，并非 MotionWeaver 主线训练计划。
