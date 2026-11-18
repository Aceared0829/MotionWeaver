// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "NNERuntimeCPU.h"

class URefPoseModelAsset;

struct MOTIONWEAVER_API FRefPoseInputTensors
{
	TConstArrayView<float> History;
	TConstArrayView<float> RootPlan;
	TConstArrayView<float> Reference;
	TConstArrayView<float> Mask;
};

/** 四输入、静态 batch=1 的 CPU 会话。Run 可在调用方拥有的工作线程执行。 */
class MOTIONWEAVER_API FRefPoseSession
{
public:
	static TSharedPtr<FRefPoseSession> Create(
		const URefPoseModelAsset& Asset, FString& OutError);

	bool Run(
		const FRefPoseInputTensors& Inputs,
		TArray<float>& OutJoints,
		double& OutMilliseconds,
		FString& OutError) const;

	int32 GetBoneCount() const { return BoneCount; }
	int32 GetHistoryFrames() const { return HistoryFrames; }
	int32 GetFutureFrames() const { return FutureFrames; }

private:
	// 不持有 UObject：TStrongObjectPtr 的析构必须在游戏线程，而会话的生命周期终点
	// 很可能落在动画工作线程（阶段三）。调用方保证 URefPoseModelAsset 比会话活得久；
	// IEnumerable 内部的 NNE 实例已经持有它用到的模型数据。
	TSharedPtr<UE::NNE::IModelCPU> Model;
	TSharedPtr<UE::NNE::IModelInstanceCPU> Instance;
	int32 BoneCount = 0;
	int32 HistoryFrames = 0;
	int32 FutureFrames = 0;
};
