// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "RefPose/RefPosePoseContract.h"
#include "RefPose/RefPoseSkeletonMap.h"
#include "RefPose/RefPoseWindowBuilder.h"

class URefPoseModelAsset;
class FRefPoseSession;
struct FPoseFrameSnapshot;

/** 一次生成的诊断指标；与视觉结果分开报告，避免用「看起来能播」代替数值。 */
struct MOTIONWEAVER_API FRefPoseGenerationMetrics
{
	/** 窗口数。 */
	int32 WindowCount = 0;
	/** 成功解算的帧数。 */
	int32 FrameCount = 0;
	/** 推理总耗时（毫秒）。 */
	double TotalInferenceMs = 0.0;
	double MaxInferenceMs = 0.0;
	/** 骨长偏差最大值（厘米）；由求解器构造方式应恒为 0。 */
	double MaxBoneLengthDeviationCm = 0.0;
	/** 位置差分退化、沿用参考旋转的骨数累计。 */
	int32 DegenerateBones = 0;
	/** 被硬参考覆盖的帧数。 */
	int32 HardReferenceFrames = 0;
};

/**
 * 单窗口生成：装配 → 推理 → 解码 → 解算局部变换。
 *
 * 单窗口是整条链里可独立复算的一步：坐标与骨长错误会在这里暴露。
 * 整段动作的多窗口调度由 `FRefPoseGroupGenerator` 负责；它复用同一个
 * `FRefPoseSession`，逐窗口调用这里的 GenerateWindow。
 */
struct MOTIONWEAVER_API FRefPoseGenerator
{
	/** 一帧输入姿态（米制 Y-up、Root 局部）。 */
	struct FPoseInput
	{
		TArray<FVector3d> PositionsMeters;
		/** 长度 = BoneCount * 6，row-major 交错。 */
		TArray<float> Rotations6D;
	};

	/** 一帧生成结果。 */
	struct FFrames
	{
		/** 每帧每骨的 UE 局部变换（相对父骨）。 */
		TArray<TArray<FTransform>> Local;
	};

	/**
	 * 用给定窗口生成 FutureFrames 帧。
	 *
	 * @param Model      已加载契约的模型资产。
	 * @param History    24 帧历史姿态。
	 * @param HistoryRoot 24 帧历史 Root（与姿态同源）。
	 * @param Future     24 帧目标姿态，仅用于提供 reference 与对照 target。
	 * @param RootOrigin 最后一帧历史 Root。
	 * @param FutureRoot 24 帧未来 Root（模型条件输入）。
	 * @param Offsets    已验证参考档位。
	 * @param RefLocal   UE 骨架的参考局部变换（骨长真值）。
	 */
	static bool GenerateWindow(
		const URefPoseModelAsset& Model,
		TConstArrayView<FPoseInput> History,
		TConstArrayView<FRefPoseWindowBuilder::FRootSample> HistoryRoot,
		TConstArrayView<FPoseInput> Future,
		const FRefPoseWindowBuilder::FRootSample& RootOrigin,
		TConstArrayView<FRefPoseWindowBuilder::FRootSample> FutureRoot,
		TConstArrayView<int32> ReferenceOffsets,
		TConstArrayView<FTransform> RefLocal,
		FFrames& OutFrames,
		FRefPoseGenerationMetrics& OutMetrics,
		FString& OutError);

	/**
	 * 复用同一个 NNE 会话生成一个窗口。
	 *
	 * 多窗口连续生成时不能每个窗口都重新 Create 会话：那会把 ONNX 反序列化
	 * 与 NNE 实例创建重复十几遍。会话由调用方持有，窗口之间只换输入。
	 */
	static bool GenerateWindow(
		const URefPoseModelAsset& Model,
		FRefPoseSession& Session,
		TConstArrayView<FPoseInput> History,
		TConstArrayView<FRefPoseWindowBuilder::FRootSample> HistoryRoot,
		TConstArrayView<FPoseInput> Future,
		const FRefPoseWindowBuilder::FRootSample& RootOrigin,
		TConstArrayView<FRefPoseWindowBuilder::FRootSample> FutureRoot,
		TConstArrayView<int32> ReferenceOffsets,
		TConstArrayView<FTransform> RefLocal,
		FFrames& OutFrames,
		FRefPoseGenerationMetrics& OutMetrics,
		FString& OutError);

	/**
	 * 构造求解器用的模型参考变换。
	 *
	 * 平移长度仍取模型契约（重定向阶段会换成 UE 骨长）；旋转取 UE 参考骨架
	 * 相对最近映射祖先的旋转。这样求解器在静止姿态下能复现 UE 的参考扭转，
	 * 而不是把每根骨都从 +X 用最短弧转过去。若这里继续用单位旋转，
	 * 肩、腕、踝这些有明显静止朝向的骨会在播放时出现不自然的扭转。
	 */
	static bool BuildSolverReference(
		const URefPoseModelAsset& Model,
		const FRefPoseSkeletonMap& Map,
		TConstArrayView<FTransform> SkeletonReference,
		TConstArrayView<int32> SkeletonParents,
		TArray<FTransform>& OutReference,
		FString& OutError);

	/**
	 * 把 UE 参考骨架上的局部姿态转成模型输入。
	 *
	 * 位置先重建到组件空间，去掉 UE 骨架根骨（root motion 由姿态组轨迹负责），
	 * 再转到该帧 Root 轨道的局部帧；姿态输入里的 Root 旋转为 identity
	 * （姿态已经表达在 Root 局部），Root 轨迹本身的朝向由 root_plan 提供。
	 * 模型有、UE 没有的骨（面部、末端）用 skeleton.json 的参考偏移挂到
	 * 最近的已映射祖先上，避免被当成零位移。
	 */
	static bool SkeletonPoseToPoseInput(
		const URefPoseModelAsset& Model,
		const FRefPoseSkeletonMap& Map,
		TConstArrayView<FTransform> SkeletonLocal,
		TConstArrayView<int32> SkeletonParents,
		const FRefPoseWindowBuilder::FRootSample& RootSample,
		FPoseInput& OutPose,
		FString& OutError);

	/**
	 * 按骨名把关键帧快照解析到 UE 参考骨架槽位。
	 *
	 * 快照的槽位顺序不一定等于参考骨架；按名字查，查不到就报错，
	 * 不按数组下标硬对。参考骨架里有、快照里没有的骨保持参考局部变换。
	 */
	static bool SnapshotToSkeletonLocal(
		const FPoseFrameSnapshot& Snapshot,
		TConstArrayView<FName> SkeletonBoneNames,
		TConstArrayView<FTransform> SkeletonReference,
		TArray<FTransform>& OutLocal,
		FString& OutError);

	/**
	 * 两个关键帧之间的局部姿态插值（位置 lerp、旋转 slerp）。
	 *
	 * 只用于给模型提供 history/reference 的已有姿态；最终播放帧仍由模型输出覆盖。
	 */
	static bool BlendSkeletonLocal(
		TConstArrayView<FTransform> A,
		TConstArrayView<FTransform> B,
		double Alpha,
		TArray<FTransform>& OutLocal,
		FString& OutError);
};
