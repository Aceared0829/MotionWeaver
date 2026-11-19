// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "RefPose/RefPoseGenerator.h"
#include "RefPose/RefPoseGenerationSettings.h"

class UPoseGroupProxyAsset;
class URefPoseModelAsset;

/**
 * 一个作者关键帧与最接近的生成帧之间的姿态差。
 *
 * 这是「锚点对齐误差」，不是模型精度：求解器会改变时长、硬参考帧会直接
 * 覆盖输出、位置差分与关键帧旋转的骨轴约定也尚未闭合（U1），
 * 这些都会计入差值。只用于让作者判断「生成的姿态有没有跑偏」，
 * 不能当成训练/推理质量指标，也不该被设成越小越好的门禁。
 */
struct MOTIONWEAVER_API FRefPoseAnchorAlignment
{
	/** 作者关键帧下标与帧号。 */
	int32 KeyIndex = INDEX_NONE;
	int32 KeyFrame = 0;
	/** 对齐到的生成帧下标。 */
	int32 GeneratedFrame = INDEX_NONE;

	/** 已映射骨（不含 Root）的局部位置差，厘米。 */
	double MaxPositionErrorCm = 0.0;
	double MeanPositionErrorCm = 0.0;
	/** 已映射骨（不含 Root）的局部旋转差，度。 */
	double MaxRotationErrorDeg = 0.0;
	double MeanRotationErrorDeg = 0.0;

	/** 参与比较的骨数（已映射且不是 Root）。 */
	int32 ComparedBoneCount = 0;
};

/** 一个姿态组从求解轨迹到 UE 可播放局部变换的完整结果。 */
struct MOTIONWEAVER_API FRefPoseGroupGenerationResult
{
	/** 每帧每骨的 UE 局部变换（按 UE 参考骨架槽位）。 */
	TArray<TArray<FTransform>> Frames;

	/** 推理与解算诊断。 */
	FRefPoseGenerationMetrics Metrics;

	/** 没有映射到 UE 骨架的模型骨数。 */
	int32 UnmappedBoneCount = 0;

	/** UE 参考骨架的骨数，便于调用方核对播放网格。 */
	int32 SkeletonBoneCount = 0;

	/** 模型骨数。 */
	int32 ModelBoneCount = 0;

	/**
	 * 非阻断告警。
	 *
	 * 求解器的「超速 / 转不过来」是创作质量闸门，不是轨迹不可用：
	 * 它仍会产出逐帧连续的轨迹样本，可以驱动模型预览。
	 * 这些条目不丢弃，但也不静默吞掉——调用方必须显示出来。
	 */
	TArray<FString> Warnings;

	/** 关键帧锚点对齐误差（参考量，不是模型精度）。 */
	TArray<FRefPoseAnchorAlignment> AnchorAlignments;

	/** 没有参与锚点比较的关键帧及原因（起点之前、无姿态等）。 */
	TArray<FString> AnchorSkips;

	/**
	 * 逐帧调试信息，与 Frames 一一对应。
	 *
	 * 编辑器用它显示「当前生成帧 → 求解帧 → 作者时间」，
	 * 也是逐帧跳转/关键帧对照的依据。求解器会做时长膨胀，
	 * 所以这三个编号不是同一条时间轴，必须分别保留。
	 */
	TArray<int32> FrameSolvedFrames;
	TArray<double> FrameAuthorFrames;

	/** 每帧相对上一帧的最大单骨局部旋转跳变（度）；首帧为 0。 */
	TArray<double> FrameMaxRotationStepDeg;

	/** 每帧跳变最大的 UE 骨骼槽位（INDEX_NONE 表示该帧无跳变）。 */
	TArray<int32> FrameWorstRotationBoneIndex;

	/** 每帧相对上一帧的最大单骨骨架空间位置跳变（厘米）；首帧为 0。 */
	TArray<double> FrameMaxPoseStepCm;

	/** 每帧位置跳变最大的 UE 骨骼槽位（INDEX_NONE 表示该帧无跳变）。 */
	TArray<int32> FrameWorstPoseBoneIndex;

	/**
	 * 与 Frames 对齐的「作者参考姿态」（UE 骨架局部变换）。
	 *
	 * 它是生成器为了装配 history / reference 而按关键帧插值出来的姿态，
	 * 只用于对照显示（参考幽灵），**不是模型输出**，也不能拿来播放冒充结果。
	 * Root 槽位在播放时会被替换成同一条求解轨迹，保证两者空间重合。
	 */
	TArray<TArray<FTransform>> AuthorFrames;

	/**
	 * 每帧「模型原始输出 vs 作者参考」的逐骨平均位置差（厘米）。
	 *
	 * 与 Frames 一一对应；硬参考帧也保留模型覆盖前的原始误差，
	 * 因此可以区分「模型本身跑偏」与「硬参考把某帧拉回作者姿态」。
	 */
	TArray<double> FramePredictionErrorCm;

	/** 该帧是否是硬参考帧（训练契约里 mask=1 的帧）。 */
	TArray<bool> FrameIsHardReference;

	/** UE 参考骨架的骨名，按槽位索引；用于把上面那个槽位翻成骨名。 */
	TArray<FName> SkeletonBoneNames;

	/** UE 参考骨架的父索引（与 Frames 槽位一致），用于骨架空间复算。 */
	TArray<int32> SkeletonParents;
};

/**
 * 姿态组 → RefPose 模型 → UE 局部变换。
 *
 * 这是编辑器「生成并播放」与自动化测试共用的唯一实现：
 * 按求解轨迹整段滑窗生成（每窗口历史 24 + 未来 24、输出 24），
 * Root 来自姿态组求解轨迹，所有窗口共用同一个播放原点。
 */
struct MOTIONWEAVER_API FRefPoseGroupGenerator
{
	static bool Generate(
		const UPoseGroupProxyAsset& Group,
		const URefPoseModelAsset& Model,
		FRefPoseGroupGenerationResult& OutResult,
		FString& OutError);

	/**
	 * 按显式生成设置生成。
	 *
	 * 与上面的重载共用同一份实现；默认重载读取
	 * `Group.RefPoseGeneration`，保证编辑器与测试看到的是同一组参数。
	 */
	static bool Generate(
		const UPoseGroupProxyAsset& Group,
		const URefPoseModelAsset& Model,
		const FRefPoseGenerationSettings& Settings,
		FRefPoseGroupGenerationResult& OutResult,
		FString& OutError);
};
