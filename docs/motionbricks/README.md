# MotionBricks：参考演示与模型技术说明

> **来源与修改说明：** 本目录基于 NVIDIA [GR00T-WholeBodyControl / MotionBricks](https://github.com/NVlabs/GR00T-WholeBodyControl/tree/main/motionbricks)，是 [AIAnimationSystem](../../README.md) 保留的模型基础与参考演示。本文件已由 AIAnimationSystem 维护者汉化并调整安装与演示说明，2026-09-07 补充来源和新仓库地址。原算法、权重及上游演示归原项目作者；本页效果展示不代表本项目 UE 插件的运行效果。

当前分支已移除机器人控制、硬件部署和遥操作组件。G1 骨架作为已有 MotionBricks 检查点所需的参考骨架保留。UE 侧的当前范围见 [Unreal 集成](../../unreal-script/README.md)。

<p align="center">
  <a href="https://nvlabs.github.io/motionbricks"><img src="https://img.shields.io/badge/项目主页-访问-blue" alt="项目主页"></a>
  <a href="motion_representation.md"><img src="https://img.shields.io/badge/文档-在线-green.svg" alt="文档"></a>
</p>

<p align="center">
  <img src="../../inference/assets/teaser_motion_bricks_three_rows.jpg" alt="MotionBricks 效果预览" width="100%">
</p>

MotionBricks 是一个面向交互式角色动画的实时生成框架，将潜变量生成模型与可组合的“智能基元”结合起来。原论文的方法、性能与演示请参阅 [上游项目主页](https://nvlabs.github.io/motionbricks/)；本仓库没有将上游性能数字作为新增 UE 通路的实测指标。

## 目录

- [新闻与路线图](#新闻与路线图)
- [效果展示](#效果展示)
- [环境配置](#环境配置)
- [交互演示快速开始](#交互演示快速开始)
- [训练](#训练)
- [动作表示与自定义数据集](#动作表示与自定义数据集)
- [相关工作](#相关工作)
- [项目结构](#项目结构)
- [已知问题](#已知问题)
- [论文引用](#论文引用)
- [许可证](#许可证)
- [联系方式](#联系方式)

## 新闻与路线图

### 新闻

- **2026-04-27（上游发布记录）**：MotionBricks 首次公开发布，包含交互演示、预训练检查点（VQ-VAE、姿态模型、根节点模型）、合成数据训练代码、动作表示文档和 GIF 展示。

### 路线图

本项目已添加自定义骨架训练入口，训练目标是固定的 78 骨 SOMA 骨架；数据摄入不经过 UE。UE 运行时适配器尚未实现。当前进度见 [项目首页](../../README.md)，UE 侧范围见 [Unreal 集成](../../unreal-script/README.md)。

## 效果展示

以下为上游随仓库提供的展示素材，原项目的完整演示和对比视频见 [MotionBricks 项目主页](https://nvlabs.github.io/motionbricks)。这些素材不是 AIAnimationSystem 新增 UE 功能的录屏。

### 效果预览

![动画效果预览](../../inference/assets/gifs/teaser_animation.gif)

### 智能移动——单一风格

| 僵尸 | 腿部受伤 |
| :---: | :---: |
| ![僵尸](../../inference/assets/gifs/loco_zombie.gif) | ![腿部受伤](../../inference/assets/gifs/loco_injured_leg.gif) |
| **躯干受伤** | **跳步** |
| ![躯干受伤](../../inference/assets/gifs/loco_injured_torso.gif) | ![跳步](../../inference/assets/gifs/loco_skipping.gif) |
| **侧向移动** | **蹲伏侧移** |
| ![侧向移动](../../inference/assets/gifs/loco_strafing.gif) | ![蹲伏侧移](../../inference/assets/gifs/loco_crouch_strafing.gif) |

### 智能移动——混合风格

| 自由风格 | 待机、行走、慢跑、奔跑 |
| :---: | :---: |
| ![自由风格](../../inference/assets/gifs/loco_freestyle.gif) | ![待机、行走、慢跑和奔跑](../../inference/assets/gifs/loco_idle_walk_jog_run.gif) |

### 智能物体交互

| 拾取长剑 | 跌落 |
| :---: | :---: |
| ![拾取长剑](../../inference/assets/gifs/obj_pickup_sword.gif) | ![跌落](../../inference/assets/gifs/obj_falling.gif) |
| **跃过长凳** | **坐下** |
| ![跃过长凳](../../inference/assets/gifs/obj_jump_bench.gif) | ![坐下](../../inference/assets/gifs/obj_sitting.gif) |
| **交互式编排** | |
| ![交互式编排](../../inference/assets/gifs/obj_interactive_authoring.gif) | |

## 环境配置

**要求：** Python 3.10 或更高版本、支持 CUDA 的显卡，以及 [Git LFS](https://git-lfs.com/)。

### 克隆仓库

MotionBricks 位于 `motionbricks/` 目录。预训练检查点、网格资源和展示 GIF 由 Git LFS 管理，因此请先安装并启用 Git LFS：

```bash
git lfs install
```

普通克隆不会自动下载约 2.2 GiB 的 MotionBricks 预训练检查点。如果只需要源代码，例如准备使用自己的数据训练，可以直接克隆：

```bash
git clone https://github.com/Aceared0829/MotionWeaver.git
cd AIAnimationSystem
```

如需运行交互演示，请在仓库根目录显式拉取检查点和 G1 参考角色网格：

```bash
git clone https://github.com/Aceared0829/MotionWeaver.git
cd AIAnimationSystem
git lfs pull --include="model-weight/base/motionbricks/**" --exclude=""
git lfs pull --include="inference/assets/skeletons/g1/meshes/**" --exclude=""  # 交互演示需要
# 以下命令在仓库根目录执行
```

拉取后检查文件大小，确认获得的不是很小的 Git LFS 指针文件：

```bash
ls -lh model-weight/base/motionbricks/G1-clip.ckpt                                     # 约 7.5 MB
ls -lh model-weight/base/motionbricks/motionbricks_vqvae/version_1/checkpoints/*.ckpt  # 约 273 MB
ls -lh model-weight/base/motionbricks/motionbricks_pose/version_1/checkpoints/*.ckpt   # 约 1.6 GB
ls -lh model-weight/base/motionbricks/motionbricks_root/version_1/checkpoints/*.ckpt   # 约 391 MB
```

如果文件只有约 1 KB，说明它是 LFS 指针。请回到仓库根目录运行 `git lfs pull --include="model-weight/base/motionbricks/**" --exclude=""` 下载真实检查点。

### 安装依赖

```bash
# 创建环境
conda create -n motionbricks python=3.10 -y
conda activate motionbricks

# 安装依赖
pip install -e ".[training,demo]"

# 仅 Linux：用于键盘输入和 MuJoCo 按键捕获规避方案
pip install pynput python-xlib
```

## 交互演示快速开始

```bash
python inference/cli/interactive_demo_g1.py
```

程序会打开中文交互界面并加载 G1 参考角色。使用键盘实时控制角色；在 3D 画面中使用鼠标可以改变相机观察方向。

默认启用 **MotionBricks G1 中文交互演示**：

- Windows：中文控制栏位于左侧，MuJoCo 3D 画面嵌入右侧，组成一个主窗口；
- Linux/macOS：由于不支持 Win32 窗口嵌入，中文控制窗口与 MuJoCo 3D 窗口分开显示；
- MuJoCo 原生英文左右侧栏默认隐藏；
- 中文控制栏提供运行/暂停、重置角色、退出演示、相机预设、接触点/关节/半透明显示开关和完整按键说明。

详细的界面说明、实现边界和故障排查参见[中文交互界面](chinese_interface.md)。若需要恢复 MuJoCo 原生英文侧栏，可运行：

```bash
python inference/cli/interactive_demo_g1.py --chinese_ui 0
```

<p align="center">
  <img src="../../inference/assets/gifs/interactive_demo.gif" alt="交互演示录屏" width="480">
</p>

### 移动控制

| 按键 | 动作 |
|---|---|
| `W` | 向前移动 |
| `A` | 向左移动 |
| `S` | 向后移动 |
| `D` | 向右移动 |

移动方向以相机为参照。在 MuJoCo 查看器中按住鼠标右键并拖动可以旋转相机。

### 动作风格

| 按键 | 风格 |
|---|---|
| `V` | 慢走 |
| `Z` | 手部支撑爬行 |
| `X` | 拳击式行走 |
| `B` | 肘部支撑爬行 |
| `R` | 潜行 |
| `T` | 受伤行走 |
| `C` | 蹲伏潜行 |
| `E` | 快乐舞步 |
| `F` | 僵尸行走 |
| `G` | 持枪行走 |
| `Q` | 惊恐行走 |

注意：爬行模式（`Z` 手部支撑爬行和 `B` 肘部支撑爬行）目前不支持纯侧向移动。

不按风格键时，默认移动方式为：没有按下移动键时**待机**，按下 WASD 时**行走**。

## 训练

项目为三个模型组件分别提供了训练脚本。脚本默认使用合成数据，并从 `model-weight/base/motionbricks/` 中保存的检查点目录加载模型配置。完整动作数据集可在 <https://bones.studio/datasets> 获取。

```bash
# 训练 VQ-VAE（动作分词器）
python training/pretrain/train_vqvae.py

# 训练姿态模型（需要预训练 VQ-VAE 检查点）
python training/pretrain/train_pose.py

# 训练根节点模型（不需要 VQ-VAE）
python training/pretrain/train_root.py
```

### 数据集

上游训练数据的介绍见 [BONES-SEED](https://huggingface.co/datasets/bones-studio/seed)。本节列出的三个上游训练脚本默认使用**合成数据**，实现位于 `motionbricks/data/synthetic_dataset.py`，用于理解和检查训练流程。UE 自定义数据训练需要实际导出并预处理的数据，见 [UE 插件与训练说明](../../unreal-script/AILocomotionSystem/README.md)。

## 动作表示与自定义数据集

动作特征表示、骨骼系统、坐标约定、归一化和特征计算流程详见[动作表示](motion_representation.md)。

如何使用自己的动作数据训练 MotionBricks，以及如何适配新的角色骨架，详见[添加自定义数据集](adding_your_own_dataset.md)。

## 相关工作

**Kimodo**：专注离线动作生成的同系列项目，与 MotionBricks 的实时运行时互补。

[项目主页](https://research.nvidia.com/labs/sil/projects/kimodo/) · [GitHub](https://github.com/nv-tlabs/kimodo)

<p align="center">
  <img src="../../inference/assets/gifs/kimodo_teaser.gif" alt="Kimodo 效果预览" width="480">
</p>

**BONES-SEED 数据集**：MotionBricks 使用的训练语料，包含由真人演员采集的 35 万段生产级动作捕捉片段。

[数据集页面](https://huggingface.co/datasets/bones-studio/seed)

<p align="center">
  <img src="../../inference/assets/gifs/bones_seed_teaser.gif" alt="BONES-SEED 效果预览" width="480">
</p>

**SOMA Retargeter**：基于 Newton 的重定向求解器，将 SOMA 捕捉数据重定向到 G1 参考骨架，用于生成 MotionBricks 训练数据。

[GitHub](https://github.com/NVIDIA/soma-retargeter)

<p align="center">
  <img src="../../inference/assets/gifs/soma_retargeter_teaser.gif" alt="SOMA Retargeter 效果预览" width="480">
</p>

## 项目结构

```text
AIAnimationSystem/
  model/MotionWeaver/       # 网络、几何、动作表示
  data/runtime/            # motionbricks.data 包
  data/tools/              # 数据准备与审核
  training/pretrain/       # 训练入口
  training/models/         # Lightning 封装，保留旧导入名
  training/evaluation/     # 质量评估
  inference/cli/           # interactive_demo_g1.py
  inference/runtime/       # 生成编排、模型加载
  inference/assets/        # MuJoCo XML、STL、上游展示
  model-weight/base/motionbricks/ # 上游权重及配套配置
  setup.py                 # 在仓库根目录安装
```

## 已知问题

- **仅 Linux/X11：** 键盘按键捕获规避方案依赖 X11（`python-xlib`）。在 Wayland、macOS 或 Windows 上，部分 MuJoCo 快捷键可能与控制键冲突。临时解决方法是让**终端保持焦点**，而不是 MuJoCo 窗口。
- **`PYTORCH_JIT=0` 会禁用按键捕获：** 使用 `PYTORCH_JIT=0` 运行会干扰 X11 按键捕获方案。如必须使用该设置，请让终端保持焦点。
- Linux/macOS 的键盘输入需要 `pynput`；Windows 使用 `keyboard` 包。

## 论文引用

如果在研究中使用 MotionBricks，请引用：

```bibtex
@misc{wang2026motionbricksscalablerealtimemotions,
      title={MotionBricks: Scalable Real-Time Motions with Modular Latent Generative Model and Smart Primitives},
      author={Tingwu Wang and Olivier Dionne and Michael De Ruyter and David Minor and Davis Rempe and Kaifeng Zhao and Mathis Petrovich and Ye Yuan and Chenran Li and Zhengyi Luo and Brian Robison and Xavier Blackwell and Bernardo Antoniazzi and Xue Bin Peng and Yuke Zhu and Simon Yuen},
      year={2026},
      eprint={2604.24833},
      archivePrefix={arXiv},
      primaryClass={cs.RO},
      url={https://arxiv.org/abs/2604.24833},
}
```

## 许可证

源代码采用 **Apache 2.0** 许可证；NVIDIA 预训练模型权重单独适用 **NVIDIA Open Model License**。适用许可、第三方资源声明及分发要求见 [引用与许可说明](../../引用与许可说明.md)、[NOTICE](../../NOTICE) 和 [许可文件](../../legal)。

Licensed by NVIDIA Corporation under the NVIDIA Open Model License.

## 联系方式

AIAnimationSystem 的中文界面、UE 插件及训练适配问题请提交至 [本仓库 Issues](https://github.com/Aceared0829/MotionWeaver/issues)。上游 MotionBricks 的反馈渠道见 [原项目](https://github.com/NVlabs/GR00T-WholeBodyControl)；本项目由本仓库独立维护。
