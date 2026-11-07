// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "MotionWeaverPoseTypes.generated.h"

/**
 * 一帧完整姿态的快照。
 *
 * 这里保存的是骨骼局部变换（相对父骨的局部空间），不是组件空间，也不是世界空间。
 * 之所以统一成局部变换：换成任何一个世界位置摆出来，父骨的相对关系都不用重算。
 */
USTRUCT(BlueprintType)
struct MOTIONWEAVER_API FMotionWeaverPoseSnapshot
{
	GENERATED_BODY()

	/** 快照格式版本；数据结构发生不兼容变更时递增，供读取端做迁移判断。 */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Pose")
	int32 FormatVersion = 1;

	/** 出处骨架的名称，仅用于校验与排错；实际骨架引用由资产持有。 */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Pose|Source")
	FName SkeletonName = NAME_None;

	/** 出处动画。可能为空（例如手工编辑的姿态）。 */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Pose|Source")
	FSoftObjectPath SourceAnimation;

	/** 出处帧号；INDEX_NONE 表示不是从动画某一帧抽出来的。 */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Pose|Source")
	int32 SourceFrame = INDEX_NONE;

	/** 出处秒数，保留原始精度，帧号四舍五入容易丢信息。 */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Pose|Source")
	double SourceTime = 0.0;

	/**
	 * 出处动画的采样帧率（每秒帧数）。
	 * 记下来是因为不同资产的帧率不一样，光有帧号无法还原时间位置。
	 */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Pose|Source")
	double SourceFrameRate = 0.0;

	/** 出处动画的采样总帧数；0 表示未知。 */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Pose|Source")
	int32 SourceTotalFrames = 0;

	/**
	 * 硬约束标记：为 true 时表示这一帧是必须原样保留的关键帧，
	 * 由调用方翻译成模型输入里的 mask 通道。
	 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Pose")
	bool bHardConstraint = false;

	/** 骨骼名称，顺序与 LocalTransforms 一一对应。 */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Pose")
	TArray<FName> BoneNames;

	/** 逐骨局部变换（相对父骨），与 BoneNames 一一对应。 */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Pose")
	TArray<FTransform> LocalTransforms;

	/** 当前代码认识的最高格式版本。读到更高版本时应当拒绝而不是硬解。 */
	static constexpr int32 CurrentFormatVersion = 1;

	/** 格式版本是否是本代码支持的。 */
	bool IsSupportedVersion() const { return FormatVersion <= CurrentFormatVersion; }

	/** 骨骼数量是否与变换数量一致。 */
	bool IsConsistent() const { return BoneNames.Num() == LocalTransforms.Num(); }

	/** 清空内容但保留版本号。 */
	void Reset()
	{
		SkeletonName = NAME_None;
		SourceAnimation.Reset();
		SourceFrame = INDEX_NONE;
		SourceTime = 0.0;
		SourceFrameRate = 0.0;
		SourceTotalFrames = 0;
		bHardConstraint = false;
		BoneNames.Reset();
		LocalTransforms.Reset();
	}
};
