# 项目来源与学习路径

MotionWeaver（原 AIAnimationSystem）从 NVIDIA [GR00T-WholeBodyControl](https://github.com/NVlabs/GR00T-WholeBodyControl) 中的 MotionBricks 开始，面向 Unreal Engine 角色动画进行开发。原始模型、算法、预训练权重和上游演示的来源与引用见 [引用与许可说明](../引用与许可说明.md)。

## 游戏动画裁剪

当前默认分支为 `main`。它保留 MotionBricks 的动作生成、VQ-VAE 动作编码、Root Motion、骨架与坐标工具、训练配置、预训练模型以及 MuJoCo 参考演示。

此前清理移除了 GEAR-SONIC 强化学习训练、Decoupled WBC、机器人硬件部署、电机与 DDS 通信、VR 遥操作、VLA 采集和相关系统服务。原始仓库内容保存在历史 `main` 分支中。适用的上游许可和第三方声明应随保留的内容继续分发。

MuJoCo 和 Unitree G1 在此用于动画预览与参考骨架。现有预训练检查点依赖 G1 拓扑，因此保留对应资源。`motionbricks` 包名、检查点目录和 `MotionBricks.exe` 表示这条参考实现；`AIAnimationSystem` 是整个派生项目的名称。

本项目增加了中文交互界面、Windows 启动器，以及自定义骨架训练入口。训练数据链路不经过 UE：跨骨架转换由离线 Python 完成，训练目标是固定的 78 骨 SOMA 骨架。UE 侧目前只保留实验宿主与空壳运行时插件，运行时推理与 CMC / Mover 接入仍待实现，见 [Unreal 集成](../unreal-script/README.md)。

## 建议学习顺序

1. 阅读 [动作表示](motionbricks/motion_representation.md)，理解骨架、Root Motion、局部姿态和特征布局。
2. 运行 [G1 交互演示](../inference/cli/interactive_demo_g1.py)，观察输入与生成动作的关系。
3. 阅读 [输入控制器](../inference/demo/controllers.py)。
4. 阅读 [逐帧推理](../inference/runtime/backbone/motion_inference.py)。
5. 阅读 [VQ-VAE](../model/MotionWeaver/tokenizer)，理解动作 Token。
6. 按 [训练说明](motionbricks/README.md#训练) 使用合成数据理解训练任务；合成数据结果不能代表真实角色动作质量。
7. 参考 [通用数据集接入](motionbricks/adding_your_own_dataset.md) 或 [SOMA 数据与双路径推理](soma-training-data-direction.md) 接入实际动画资产。

本页由 AIAnimationSystem 维护者于 2026-09-07 从原根 README 整理并补充；上游技术内容的来源与版权保留。
