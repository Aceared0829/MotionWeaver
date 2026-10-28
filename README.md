# MotionWeaver

MotionWeaver（原 AIAnimationSystem）是面向 Unreal Engine 的 AI 角色动画实验项目，探索从动画数据训练到实时姿态生成的完整流程。

本项目基于 NVIDIA [GR00T-WholeBodyControl](https://github.com/NVlabs/GR00T-WholeBodyControl) 仓库中的 [MotionBricks](https://nvlabs.github.io/motionbricks/) 开发，由本仓库独立维护。MotionBricks 的模型架构、动作表示、原有训练与推理代码、预训练权重和上游演示属于原项目成果；本项目在其基础上面向游戏动画做裁剪、中文交互界面与 Windows 启动器适配，并添加实验性的 UE 动画导出和自定义骨架训练入口。

> **实验阶段：** 已有 UE 数据、训练与 FP32 导出工具；`AIAnimation` Inference Lab 已在独立 GASP 对照场景中验证 AnimGraph 节点、动画工作线程 DirectML 推理和连续播放修复。它不是正式游戏运行时，也未完成完整玩法、打包、多角色并发、网络或 CMC / Mover 接入。本项目不代表 NVIDIA 官方产品，也不表示获得 NVIDIA 的认可或背书。

当前先完成 Locomotion、跳跃和蹲伏的数据与动画流程；项目名称覆盖更广泛的角色动画方向，后续动作范围以开发方案与实际验证为准。

[UE 插件与训练](unreal-script/AILocomotionSystem/README.md) · [AIAnimation 实验推理](unreal-script/AIAnimation/README.md) · [第一阶段开发方案](unreal-script/AILocomotionSystem/LocomotionPlan.md) · [MotionBricks 演示与技术说明](docs/motionbricks/README.md) · [来源、引用与许可](引用与许可说明.md)

## 当前进度

| 内容 | 当前状态 |
| --- | --- |
| MotionBricks G1 参考演示 | 保留上游模型与 MuJoCo 预览流程，增加中文界面和 Windows 启动器；不等同于 UE 运行时效果 |
| UE 动画导出 | 已有实验性 `AILocomotionDataset` 编辑器插件，从 `AnimSequence` 导出骨架和动作数据 |
| 自定义 UE 骨架训练 | 已有数据预处理、VQ-VAE / Pose / Root 训练、检查点保存与续训入口 |
| 已记录的验证 | UE 5.8.2 编译与链接、程序生成动画夹具上的 CPU 小网络训练测试；详见插件说明 |
| 真实资产与生成质量 | 历史实验见插件的 TrainingResults；本次迁移使用受控夹具验证功能，不重新评定真实动作生成质量 |
| UE 实验推理 | `AIAnimation` 在 GASP 独立对照场景中完成 ONNX 导入、AnimGraph 重建节点、动画工作线程 DirectML 推理、延迟融合播放和性能报告；详见实验说明 |
| 跑酷参考姿态实验 | 可在本地 HTML 手选关键帧，对比软条件重建与融合后硬约束；已验证单条低障碍动画的参考帧精确到位，仍有邻域连续性和姿态偏差限制 |
| SOMA 全库训练视图 | 已建成 71,127 条 fps120 四场景（A 27,182 / B 14,235 / C 2,621 / D 27,089），训练端可直接读取四键 NPZ |
| 分词器（VQ-VAE） | 已在全库上训练至 100,000 步，关节 RMSE 稳定在 84–86 cm；但码本在 20k 步后塌缩至 perplexity 1.000，是当前主要瓶颈 |
| 统一评测 | `benchmark/` 收敛原先散在三处的评测代码，以 `evaluate_all()` 为唯一入口，官方已有的指标原语直接复用 |
| 损失函数与评测口径 | 已修正一处使训练与评测喂不同归一化分布的口径缺陷（−11.3%）；关节位置损失经 17 组同步数对照判定无效应，默认关闭 |
| 正式 UE 运行时动画 | 资源生命周期、游戏工具函数、统一人形重定向、CMC / Mover、多人并发与联机接入均为后续工作 |

现有 G1 权重与参考骨架绑定。自定义 UE 训练入口不支持直接加载 G1 权重微调，原 G1 演示也不能直接预览新的 UE 骨架检查点。

## 快速开始

### 获取仓库

安装 Git LFS 后执行：

```bash
git lfs install
git clone https://github.com/Aceared0829/MotionWeaver.git
cd MotionWeaver
```

当前默认分支为 `main`。预训练检查点默认不自动下载；按下面的使用场景准备环境和模型。

### UE 动画导出与训练

将 `unreal-script/AILocomotionSystem/AILocomotionDataset/` 复制到 UE 项目的 `Plugins/AILocomotionDataset/`，编译 Editor 目标并启用 **AI Locomotion Dataset**。在编辑器中选择动画资产，通过 **工具 → AI Locomotion：导出选中动画** 生成训练数据。

Python 训练环境使用 Python 3.12；完整安装命令、骨架要求、预处理与训练命令见 [UE 插件与训练说明](unreal-script/AILocomotionSystem/README.md)。这条路线使用自己的动画数据，不需要下载 G1 演示权重。

`AILocomotionSystem` 与 `AILocomotionDataset` 目前仍是插件目录和技术标识；仓库对外名称为 **AIAnimationSystem**。

### GASP 实验推理

`unreal-script/AIAnimation/` 提供单独的实验插件和可重复对照场景。它使用已知 `AnimSequence` 作为输入重建 Root 相对身体姿态，当前默认采用固定 4 帧推理步长、8 帧播放延迟和窗口融合。安装、构建、GPU 数值校验、基准测试、连续性图表及边界说明见 [AIAnimation 实验说明](unreal-script/AIAnimation/README.md)。

### 手选跑酷参考姿态

从仓库根目录运行 `./inference/profiling/Start-PoseReferenceLab.ps1`，在本地页面逐帧选姿态，再运行真实模型对照。需要本机 V2 检查点和准备好的原始数据；这些数据与权重不随本次源码提交发布。

[实验室操作、软/硬对照与限制](inference/profiling/pose-reference-lab.md)记录了用户所选六帧的结果：平均关节误差 11.322→6.903 cm，参考帧通过后处理等式约束精确满足；加速度误差略升，不能宣称解决抽搐。编码器仍读取完整已知窗口，代理模式还读取已知未来；尚非稀疏参考生成未知过渡，也未接入 UE 跑酷执行逻辑。

### MotionBricks 参考演示

该演示使用 G1 参考骨架、MuJoCo 和预训练权重。按现有演示环境说明准备 Python 3.10、支持 CUDA 的显卡及依赖，在仓库根目录执行：

```bash
git lfs pull --include="model-weight/base/motionbricks/**" --exclude=""
git lfs pull --include="inference/assets/skeletons/g1/meshes/**" --exclude=""
# 以下命令在仓库根目录执行
conda create -n motionbricks python=3.10 -y
conda activate motionbricks
pip install -e ".[training,demo]"
python inference/cli/interactive_demo_g1.py
```

Windows 下默认打开中文单窗口界面，其他平台在不能嵌入 MuJoCo 窗口时回退为两个窗口。详见 [演示说明](docs/motionbricks/README.md) 和 [中文交互界面](docs/motionbricks/chinese_interface.md)。

仓库根目录的 `MotionBricks.exe` 是该参考演示的 Windows 启动器，固定读取根目录 `.venv` 中的环境；上面的 Conda 环境不会自动供 EXE 使用。它不包含 Python、模型和依赖，也不是 UE 插件启动器。

## 最近的实测结论

2026-10 的一轮实验给出两条结论，都记在 [技术报告 §9](paper/main.html) 与
[关节误差文档](docs/soma-tokenizer-joint-error-options.md)：

1. **评测口径曾经是错的。** 训练与评测喂给模型的是两种不同的归一化分布
   （`--dataset` 落盘前已标准化，`--soma_views` 产出原始特征，而
   `is_normalized` 被写死成一个默认值）。后果不是「训练变慢」，而是
   训练目标与评测目标自始至终不是同一个量：30,000 步之后关节误差从 108 cm
   断崖到 310 cm。把口径对齐后，同一评测口径下关节 RMSE 由 94.54 cm 降至
   **83.85 cm（−11.3%）**，这是本项目至今最大的一次单点收益，且不来自任何损失项。
2. **把验收指标写进损失的尝试失败了。** 在 17 组同步数对照下，关节位置损失的
   效应为 **+0.10 cm**，小于同配置换种子造成的 0.56 cm 抖动（符号检验 p=0.75）。
   原因是码本在 20,000 步后塌缩至 `perplexity = 1.000`，每批所有 token 量化到
   同一码字，分词器退化为自编码器——码字里没有可供优化的信息。

下一步的主线因此不是继续调损失，而是解决码本塌缩。验收判据写在
[技术报告 §11](paper/main.html)：`perplexity_pose` 稳定 > 8，且关节 RMSE 不退化。

## 开发方向

第一阶段由 CMC / Mover 负责移动模拟，模型根据实际运动状态与未来轨迹生成角色姿态，再适配目标骨架：

```text
训练：UE 动画资产 → 采样与数据转换 → 模型训练 → 模型包

运行时目标：玩家 / AI 输入 → CMC 或 Mover → 运动状态与轨迹条件
                                                   ↓
                                      动画模型 → 骨架适配 → UE 姿态
```

运行时部分为开发目标。多人联机、统一人形模型、重定向与接触修正的职责和验收要求见 [第一阶段开发方案](unreal-script/AILocomotionSystem/LocomotionPlan.md)。

## 仓库与文档

已按数据、训练、模型、推理职责重组。模型源码位于 `model/MotionWeaver/`，Python 包名为 `motionweaver`；数据、训练和推理分别使用 `data.runtime`、`training`、`inference`。安装入口为仓库根目录，旧检查点导入集中兼容；已有数据和权重保留。目录与测试说明见 [仓库结构](docs/repository-layout.md)。

```text
data/               runtime/ · tools/ · prepared/（本地）
training/           pretrain/ · posttrain/ · common/ · models/ · evaluation/ · configs/ · runs/（本地）
model/MotionWeaver/ 网络、几何、动作表示与共享实现
model-weight/       base/motionbricks/ · 自定义发布产物
inference/          runtime/ · export/ · profiling/ · cli/ · demo/ · assets/
unreal-script/      AILocomotionSystem/ · python/
unreal-sample/      UE 推理/动画工具链实验宿主源码与资产交付边界（Content 暂缓）
benchmark/          统一评测：recon 库、CLI 与四臂对比（唯一评测入口）
paper/              技术报告（main.html → PDF）与软著材料
tests/              单元、训练、导出与目录回归测试
tools/compat/       历史命令与检查点导入兼容源码
.build/             python/ 安装元数据 · experiments/ 本地实验输出
```

| 位置 | 用途 |
| --- | --- |
| [unreal-sample](unreal-sample/README.md) | UE 5.8 实验宿主 `UMWSamplePreview` 与 Content 暂缓发布的边界；当前项目尚未接入模型推理 |
| [unreal-script](unreal-script/README.md) | UE 插件安装、动画导出、数据约定与训练说明 |
| [模型与参考演示说明](docs/motionbricks/README.md) | 上游模型技术说明与 G1 参考演示 |
| [动作表示](docs/motionbricks/motion_representation.md) | 骨架、Root Motion、姿态特征和坐标约定 |
| [接入自有数据集](docs/motionbricks/adding_your_own_dataset.md) | MotionBricks 通用数据集接入；UE 资产优先使用上面的插件说明 |
| [学习与项目历史](docs/project-background.md) | 源码阅读顺序、游戏动画裁剪范围与上游关系 |
| [实验记录](docs/experiments/README.md) | 可提交的逐次数据、训练、评估与 UE 验证记录及证据索引 |
| [技术报告](paper/main.html) | 完整技术报告源码；PDF 见 `paper/MotionWeaver-technical-report.pdf`（31 页） |
| [软著材料](paper/软著/README.md) | 软件著作权登记材料：说明书、源程序文档、能力清单与一致性检查 |
| [关节误差与方案 A 结论文档](docs/soma-tokenizer-joint-error-options.md) | 归一化口径缺陷的定位与修复、关节位置损失的 17 组对照、码本塌缩证据 |
| [分词器评测](docs/soma-tokenizer-benchmark.md) | 统一 benchmark 的四臂结果与口径边界 |
| [权重清单](docs/assets/plan-a-final/WEIGHTS_MANIFEST.md) | 长期保留的 25 个 run 的配置、步数、最优指标与位置 |
| [launcher](launcher) | Windows 参考演示启动器源码 |

## 来源与许可

- **上游来源：** NVIDIA [GR00T-WholeBodyControl / MotionBricks](https://github.com/NVlabs/GR00T-WholeBodyControl/tree/main/motionbricks)。原作者、版权及论文信息予以保留；上游展示不能作为本项目新增 UE 功能的验证结果。
- **源代码：** 采用 Apache License 2.0，保留适用的版权与变更说明。
- **NVIDIA 预训练权重：** 单独适用 NVIDIA Open Model License，不能按源代码的 Apache 2.0 授权理解。
- **第三方资源与动画数据：** 依照各自授权使用；本仓库的代码许可证不授予外部动画资产、网格或数据集的使用权。

Licensed by NVIDIA Corporation under the NVIDIA Open Model License.

完整来源、许可文本入口、第三方声明和论文引用见 [引用与许可说明](引用与许可说明.md)、[NOTICE](NOTICE) 与 [LICENSE](LICENSE)。

---

本文件由 AIAnimationSystem 维护者基于上游 README 改写；2026-09-07 更新项目命名、
使用入口、开发状态及来源声明；2026-10-28 补充 SOMA 全库训练视图、统一评测、
分词器现状与最近的实测结论。
