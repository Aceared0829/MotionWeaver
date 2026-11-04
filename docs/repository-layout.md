# 仓库目录与维护说明

本页说明当前职责目录、安装入口和兼容约定。2026-09-12 的迁移映射、验收结果及当时工作区状态见 [历史迁移记录](history/repository-migration-20260912.md)。

## 当前布局

```text
MotionWeaver/
├── data/
│   ├── runtime/                  # data.runtime：骨架、数据集、格式契约
│   ├── tools/                    # 数据准备、批次处理、审核和入库
│   │   └── review_web/           # 审核网页资源
│   └── prepared/                 # 已有本地 UE 数据，不提交
├── training/
│   ├── pretrain/                 # train_unreal、train_vqvae、train_pose、train_root
│   ├── posttrain/                # 扩展位置；尚无独立后训练算法
│   ├── models/
│   │   ├── vqvae/                # Lightning VQ-VAE 封装
│   │   └── backbone/             # Lightning Pose、Root 封装
│   ├── common/                   # 质量训练、有效帧掩码、采样器
│   │   └── optim/                # 调度器及训练日志工具
│   ├── evaluation/               # 重建评估、容量诊断、评估画廊
│   ├── configs/pretrain/         # motionweaver_{vqvae,pose,root}.yaml 训练模板
│   └── runs/                     # 本地训练检查点与实验产物，不提交
├── model/
│   └── MotionWeaver/
│       ├── tokenizer/neural_modules/ # 编码器、码本、解码器
│       ├── backbone/             # Pose、Root 网络及共享采样
│       ├── representation/
│       │   ├── core/            # 动作表示、骨架、变换与数学
│       │   └── helper/          # 共享表示、特征与命令行工具
│       ├── geometry/             # 几何计算
│       └── repository.py         # 与当前工作目录无关的资源定位
├── model-weight/
│   └── base/motionbricks/        # 原 out：权重及绑定的配置、骨架、统计量
├── inference/
│   ├── runtime/
│   │   ├── checkpoint.py         # VQ-VAE 契约加载；评估与导出共用
│   │   ├── backbone/             # 上游生成编排
│   │   └── experiment/           # G1 旧模型加载兼容流程
│   ├── export/                   # FP32 完整/decoder-only 导出
│   ├── profiling/                # CUDA 神经网络性能测量
│   ├── cli/                      # G1 演示入口
│   ├── demo/                     # 演示控制器、界面、MuJoCo 集成
│   └── assets/                   # G1 骨架、网格与上游预览素材
├── unreal-script/
│   ├── MotionWeaver/             # Sample 插件的同步镜像
│   └── Tools/                    # Sync-MotionWeaver.ps1 镜像同步脚本
├── unreal-sample/                # AIMotionSample：UE 5.8 推理/动画工具链实验宿主（GASP 副本；Content 不入库）
├── tests/                       # 回归测试与受控动画夹具
├── docs/                        # 架构、迁移与技术说明
├── launcher/                    # Windows 启动器源码
├── tools/                       # 日常工具；archive/ 保存一次性迁移脚本
│   └── compat/motionbricks/      # 历史 Python 导入与 CLI 兼容源码
├── .build/                      # 本地产物默认忽略；原有 4 份验收报告保留追踪
│   ├── python/                  # 自动生成的安装元数据与旧元数据备份
│   └── experiments/             # 原 output/：实验报告、导出包与预览
├── setup.py                     # 唯一有效安装入口
└── pyproject.toml
```

`unreal-sample/AIMotionSample/` 是 Epic GASP 官方示例工程的副本（`EpicSampleNameHash` 1673045636），用于后续接入 UE 内推理及动画工具链；不处理动作数据，也尚未接入模型或推理 AnimGraph。当前只有 `Content/`（3,898 个文件，不入 Git）、`Config/`、`Build/`、`Binaries/`，**没有自有 `Source/` 与 `Plugins/`**，`Binaries` 仍是 `GameAnimationSample` 模块名，尚未按新工程重编译。因此干净检出或本地副本目前都不能编译出自有游戏模块。上一代宿主 `UMWSamplePreview`（52 个追踪文件，含 `Source/` 下 45 个 C++/配置与 `Plugins/MotionWeaver/`）已于 2026-11 一并移除，代码从 git 历史取回。`unreal-script/MotionWeaver/` 是当前唯一现存的 MotionWeaver 插件副本，原同步镜像关系因开发源消失而暂停。

2026-11 移除了 `unreal-script/AIAnimation/`、`AIAnimationMotionMatching/`、`AILocomotionSystem/`、`python/` 与 `MotionWeaverCMCPreview/`：旧推理插件是 79 骨 / 30 fps / 单输入 VQ 重建，与当前封盘的 78 骨 SOMA / 120 fps / 四输入 refpose v3 契约不兼容；`AILocomotionDataset` 的 79 骨 UEFN JSON 产出没有下游消费者；`python/` 的 UE 内 IK Retargeter 脚本依赖的上游数据盘已不存在。数据摄入链路已完全不经过 UE，详见 [Unreal 集成](../unreal-script/README.md) 与 [Unreal 实验宿主说明](../unreal-sample/README.md)。

`data/raw/`、`model-weight/pretrained/`、`finetuned/`、`exported/` 按实际产物需要创建，已配置本地忽略规则。目录名不表示训练、后训练或示例已经完成。

## 安装、兼容与依赖

已有 `.venv` 且依赖齐备时，可重新安装本仓库的可编辑包：

```powershell
uv pip install --python .venv/Scripts/python.exe --no-deps -e .
.venv/Scripts/python.exe -m training.pretrain.train_unreal --help
.venv/Scripts/python.exe -m inference.export.export_unreal_inference --help
.venv/Scripts/python.exe -m data.tools.motion_review_server --help
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

新环境可在根目录安装 `.[training]` 或 `.[training,demo]`；GPU PyTorch 应使用适合目标硬件的构建。旧 `motionbricks/setup.py` 会明确提示安装根目录，避免误装空包。

根目录安装的发行包为 `motionweaver`，`setup.py` 将它映射到唯一的模型源码目录 `model/MotionWeaver/`。模型使用 `motionweaver.tokenizer`、`motionweaver.backbone`、`motionweaver.representation`、`motionweaver.geometry`；数据、训练、推理直接使用 `data.runtime`、`training.*`、`inference.*`。MuJoCo 专用辅助代码位于 `inference/demo/mujoco_helper.py`。

新训练从 `training/configs/pretrain/motionweaver_*.yaml` 读取模板，不再依赖上游权重目录中的 hparams。模板仅保留当前训练实际使用的配置；训练检查点和新导出包使用新的 Hydra 类路径。

`motionweaver.legacy` 集中维护历史名称映射，`tools/compat/motionbricks/__init__.py` 仅为旧检查点/配置注册延迟导入别名，不复制网络、不提前导入训练依赖。读取旧配置时仅转换 Hydra `_target_` 类路径，不改动权重二进制或数据契约。上游 G1 参考资源与历史本机路径仍保留原名。

`setup.py` 将 egg-info 固定生成到 `.build/python/`，首次安装时自动创建该目录。兼容包直接从 `tools/compat/motionbricks/` 安装，不依赖根目录旧入口。

原先已提交的 4 份实验验收报告随目录迁入 `.build/experiments/`，保留 Git 追踪；其余实验产物与安装元数据继续忽略。不得为发布实验报告而提交整个 `.build/`。

新入口支持从任意工作目录执行，前提是已经安装包。wheel 不携带大权重、数据与 UE 工程，使用这些仓库资源时设置 `AIANIMATION_ROOT` 指向完整检出。

UE VQ-VAE 导出与训练评估入口分开；相关依赖隔离由 `tests/test_repository_layout.py` 覆盖。G1 `runtime/experiment/` 仍兼容上游 Lightning 模型封装，尚未重写为独立部署后端。

本机根目录的 `motionbricks/`、`output/` 仅为目录连接，分别指向 `tools/compat/motionbricks/`、`.build/experiments/`，不重复保存文件；VS Code 工作区隐藏这两个旧入口。兼容目录内的 `unreal_data`、`unreal_runs`、`out`、`assets` 连接到对应数据、训练、权重与资产目录。它们保留旧绝对路径读取能力。重建工具为 `tools/install_legacy_paths.ps1`；换机器仍需恢复实际数据，旧机器绝对路径不会自动变成可移植路径。旧 `scripts/*.py` 为委托入口；新代码不依赖旧脚本目录。

## 日常检查与历史审计

从仓库根目录执行 CLI 检查，验证新旧入口能在仓库外工作目录显示帮助：

```powershell
.venv/Scripts/python.exe tools/verify_repository_layout.py --output .build/experiments/repository-layout-cli.json
```

默认检查不读取迁移当天的本地产物清单，不要求训练数据和权重保持历史大小及修改时间。

需要对照迁移快照时，显式指定原回执目录：

```powershell
.venv/Scripts/python.exe tools/verify_repository_layout.py --output .build/experiments/repository-migration-audit.json --migration-audit .build/repository-migration-20260912
```

显式审计要求回执中的 `moves.json` 和 `artifact-inventory.json` 均存在；检查缺失文件、大小和修改时间差异，并在有差异时返回非零。它只比较历史元数据，不能据此判定内容损坏或替代 SHA-256 校验。外部数据盘离线、正常训练或恢复 LFS 权重都可能产生差异。

MotionWeaver 的唯一开发源原为 `unreal-sample/UMWSamplePreview/Plugins/MotionWeaver/`，随该宿主于 2026-11 移除；`unreal-script/MotionWeaver/` 现为唯一现存副本。恢复开发源/镜像工作流的步骤见 [插件镜像说明](../unreal-script/README.md#motionweaver-插件镜像)。

## 本地产物保留边界

`.build/` 混有可重建产物与历史证据，不应整目录清理。编译中间产物、已完成下载的分片和临时安装目录可单独评估清理；迁移回执、实验原始证据及 `user-edits/` 等备份需先核对用途和独有内容。文档中的历史本机路径不保证今天仍存在。

工具与文档归档保留旧 Python 名称、CLI 委托和目录连接，不移动本地回执、训练数据、权重或资产备份。
