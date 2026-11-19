// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "RefPose/RefPoseGenerator.h"

class UPoseGroupProxyAsset;
class URefPoseModelAsset;

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
};
