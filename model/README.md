# 模型源码

`motionbricks/` 保留 NVIDIA MotionBricks 的网络、量化器、几何与动作表示。上游 Root 网络仍保留以兼容 G1 参考流程，不代表 UE pose-only 数据允许训练 Root。

Python 安装名称仍为 `motionbricks`。根目录 `setup.py` 将数据、训练与推理子包映射到各自职责目录，保持 Hydra `_target_` 和历史检查点可解析。网络导入不要求执行训练脚本。

`helper/data_training_util.py` 的特征转换被训练和推理共用，暂保留兼容名称和唯一实现。`helper/pl_util.py` 只构建动作表示，并不导入 Lightning。

`motionweaver/` 记录基于 MotionBricks 架构适配 UE 实时角色运动的新模型方向；给定 Root 的姿态推理源码在 `inference/runtime/motionweaver.py`。20,000 步 Pose Token 权重已在 A800 训练并完成本地骨架推理评估，但仍未通过脚滑、Run/Crouch 姿态和 CMC 蹲伏响应的质量门槛，也没有 MotionWeaver UE 推理包。现有玩家试玩的 `ReferenceGuidedPose` 是另一条轻量对照路线，类定义在 `training/pretrain/train_conditioned_pose.py`，不能仅改名当作 MotionWeaver。旧 Pose Token 路线的记录见 [Actor Root 实验](../docs/experiments/2026-09-28-motionweaver-a800-actor-root.md)；当前主线与官方的差别见 [对照表](../docs/refpose-vs-motionbricks.md)。
