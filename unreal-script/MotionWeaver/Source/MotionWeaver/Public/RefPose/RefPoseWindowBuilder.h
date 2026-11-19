// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "RefPose/RefPosePoseContract.h"

/**
 * RefPose 输入装配：把「一段已解码的姿态窗口 + 一个未来 Root 计划 + 参考帧」
 * 拼成模型要的四块张量。
 *
 * 与 Python 侧 `inference/export/refpose/inputs.py:build_sample` 是同一份契约的两份
 * 实现。C++ 这份只做一个窗口，不做训练侧的采样/统计，因此可以与 Python 逐元素比对。
 *
 * 四种已验证的参考档位（训练只见过这四种）：
 *   ()      无参考
 *   (12,)   窗口中间
 *   (23,)   窗口末端
 *   (12,23) 中间 + 末端
 * 其它组合属于未覆盖档位，界面必须显式标注，不能默认承诺质量。
 */
struct MOTIONWEAVER_API FRefPoseWindowBuilder
{
	static constexpr int32 HistoryFrames = 24;
	static constexpr int32 FutureFrames = 24;

	/** 统计量：只作用于位置通道。 */
	struct FNormalization
	{
		TArray<float> Mean;  // BoneCount * 3
		TArray<float> Std;   // BoneCount * 3

		bool IsValid(int32 BoneCount) const
		{
			return Mean.Num() == BoneCount * 3 && Std.Num() == BoneCount * 3;
		}
	};

	/** 一帧姿态：位置（米制 Y-up、Root 局部）+ 旋转（6D，不归一化）。 */
	struct FPoseFrame
	{
		TArray<FVector3d> PositionsMeters;
		/** 长度 = BoneCount * 6，按 row-major 交错。 */
		TArray<float> Rotations6D;
	};

	/** 一段 Root 轨道样本：位置（米制）+ 四元数（xyzw）。 */
	struct FRootSample
	{
		FVector3d PositionMeters = FVector3d::ZeroVector;
		FQuat4d Rotation = FQuat4d::Identity;
	};

	/**
	 * 装配结果。每帧特征是「逐骨 9 维」：位置 3 维 + 6D 旋转 6 维。
	 *
	 * 这段排布必须与训练侧 build_sample 的 reshape 结果逐元素一致；
	 * manifest 的 layout 段写成「先全部位置再全部旋转」，与训练实际的逐骨
	 * 打包不一致，以实现与夹具为准（记录见
	 * docs/experiments/2026-11-18-refpose-pose-group-playback.md）。
	 */
	struct FWindow
	{
		/** [HistoryFrames, BoneCount*9 + 7] */
		TArray<float> History;
		/** [FutureFrames, 7] */
		TArray<float> RootPlan;
		/** [FutureFrames, BoneCount*9] */
		TArray<float> Reference;
		/** [FutureFrames, 1] */
		TArray<float> Mask;
		/** 目标帧特征 [FutureFrames, BoneCount*9]，用于与模型输出对照。 */
		TArray<float> Target;

		bool IsSized(int32 BoneCount) const
		{
			const int32 Features = BoneCount * 9;
			return History.Num() == HistoryFrames * (Features + 7)
				&& RootPlan.Num() == FutureFrames * 7
				&& Reference.Num() == FutureFrames * Features
				&& Mask.Num() == FutureFrames
				&& Target.Num() == FutureFrames * Features;
		}
	};

	/**
	 * 把世界轨道的 Root 转到「相对 origin」的局部表示（位置与朝向都相对）。
	 * origin 取最后一帧历史 Root，与训练侧一致。
	 */
	static TArray<FRootSample> RootLocal(
		TConstArrayView<FRootSample> Track,
		const FRootSample& Origin);

	/**
	 * 组装一个窗口。
	 *
	 * @param BoneCount         骨骼数量（必须与模型契约一致）。
	 * @param History           24 帧历史姿态。
	 * @param HistoryRoot       24 帧历史 Root（世界轨道；内部按 RootOrigin 相对化）。
	 * @param Future            24 帧目标姿态（用于 reference/target；不是模型输出）。
	 * @param RootOrigin        最后一帧历史 Root，即 history[HistoryFrames-1]。
	 * @param FutureRoot        24 帧未来 Root（模型条件输入）。
	 * @param ReferenceOffsets  已验证档位之一；非法值会被拒绝。
	 * @param Norm              归一化统计量。
	 */
	static bool Build(
		int32 BoneCount,
		TConstArrayView<FPoseFrame> History,
		TConstArrayView<FRootSample> HistoryRoot,
		TConstArrayView<FPoseFrame> Future,
		const FRootSample& RootOrigin,
		TConstArrayView<FRootSample> FutureRoot,
		TConstArrayView<int32> ReferenceOffsets,
		const FNormalization& Norm,
		FWindow& OutWindow,
		FString& OutError);
};
