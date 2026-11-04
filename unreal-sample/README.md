# Unreal 实验宿主与资产边界

`AIMotionSample/AIMotionSample.uproject` 是 UE 5.8 的实验宿主，用于逐步测试 UE 内实际推理及动画工具链，验证后再考虑正式使用；**不承担动作数据导入、清洗或数据集处理**。

## 当前状态：GASP 副本 + 已挂载 MotionWeaver 插件

`AIMotionSample` 是 Epic 官方 GASP（Game Animation Sample）工程的副本，`EpicSampleNameHash` 为 `1673045636`：

| 项 | 当前状态 |
| --- | --- |
| `Content/` | 3,898 个文件，与 `D:/GameAnimationSample/Content` 一致；不入 Git |
| `Config/` | 7 个 ini 随副本带入；`DefaultEditor.ini` 由 UE 写入本机预览配置，不入 Git |
| `Source/` | UE 生成的空项目模块（`AIMotionSample` + 两个 Target），已入库 |
| `Plugins/MotionWeaver/` | 已挂载并在 `.uproject` 中启用；本目录是唯一开发源 |
| `Plugins/Developer/RiderLink/` | Rider 自动装入的 IDE 桥接插件（约 500 文件 / 140 MB），**不入 Git** |
| `Binaries/`、`Intermediate/`、`Saved/`、`DerivedDataCache/` | UE 生成物，不入 Git |

该宿主目前能编译 UE 项目模块与 `MotionWeaver` 插件，但项目模块仍是 UE 新建工程的空壳，**尚不含 CMC 角色与动画逻辑**，也尚未接入模型包或推理 AnimGraph。默认地图为 `/Game/Levels/DefaultLevel`。

上一代宿主 `UMWSamplePreview` 已于 2026-11 移除：其 52 个 git 追踪文件（`Source/` 45 个 C++/配置文件、`Plugins/MotionWeaver/`、`MOTIONMATCHING_MIGRATION.md` 与 `Config/`）按维护者决定一并删除，不迁入 `AIMotionSample`。需要查阅这些代码时从 git 历史取回（`git show e7330af:unreal-sample/UMWSamplePreview/<路径>`）。

原 `MotionMatchingInCpp` 的 Gameplay 源码曾迁入 `UMWSamplePreview` 的项目模块，随该工程一并移除；调整目录不改变这些外部代码与动画资产的原有授权。

外部 `D:/GameAnimationSample` 是已有数据流程使用的工程，不迁入本宿主。

## 已移除的历史 GASP 宿主实验

`MotionWeaverCMCPreview` 插件及其 4 个驱动脚本（`create_cmc_preview.py`、`add_stance_model_preview.py`、`verify_cmc_preview.py`、`Run-StanceModelPreview.ps1`）已于 2026-11 移除。它们只服务于外部 `D:/GameAnimationSample` 宿主的 CMC 观测与 B1 站蹲预览，生成的 `/MotionWeaverCMCPreview/` 资产从未入库。当时的结论仍可从执行记录查阅：[UE CMC 单机演示实验](../docs/experiments/2026-09-28-ue-cmc-preview.md)、[UE B1 在线预览实验](../docs/experiments/2026-09-28-ue-b1-online-preview.md)；代码本体从 git 历史取回（`git show e7330af:unreal-script/MotionWeaverCMCPreview/...`）。

`inspect_gasp_level.py` 保留：它是只读的 GASP 关卡与 CMC 角色检查脚本，不依赖任何已移除的插件。
