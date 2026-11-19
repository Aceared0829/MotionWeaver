// Copyright ZhaoZining. All Rights Reserved.

#include "RefPose/RefPoseModel.h"

#include "Misc/FileHelper.h"
#include "NNEModelData.h"
#include "RefPose/RefPoseWindowBuilder.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

namespace
{
	/** skeleton.json 里的 position 是参考姿态的父级相对平移（米，Y-up）。 */
	bool ParseBones(const TSharedPtr<FJsonObject>& Root, TArray<FRefPoseBoneContract>& OutBones, FString& OutError)
	{
		const TArray<TSharedPtr<FJsonValue>>* Bones = nullptr;
		if (!Root.IsValid() || !Root->TryGetArrayField(TEXT("bones"), Bones) || Bones == nullptr)
		{
			OutError = TEXT("skeleton.json 缺少 bones 数组。");
			return false;
		}

		OutBones.Reset(Bones->Num());
		for (int32 Index = 0; Index < Bones->Num(); ++Index)
		{
			const TSharedPtr<FJsonObject> Bone = (*Bones)[Index]->AsObject();
			if (!Bone.IsValid())
			{
				OutError = FString::Printf(TEXT("skeleton.json 第 %d 节不是对象。"), Index);
				return false;
			}

			FString Name;
			double Parent = INDEX_NONE;
			const TArray<TSharedPtr<FJsonValue>>* Position = nullptr;
			if (!Bone->TryGetStringField(TEXT("name"), Name))
			{
				OutError = FString::Printf(TEXT("skeleton.json 第 %d 节缺 name。"), Index);
				return false;
			}
			if (!Bone->TryGetNumberField(TEXT("parent"), Parent))
			{
				OutError = FString::Printf(TEXT("skeleton.json %s 缺 parent。"), *Name);
				return false;
			}
			if (!Bone->TryGetArrayField(TEXT("position"), Position)
				|| Position == nullptr || Position->Num() != 3)
			{
				OutError = FString::Printf(TEXT("skeleton.json %s 的 position 不是三元组。"), *Name);
				return false;
			}

			FRefPoseBoneContract Contract;
			Contract.Name = FName(*Name);
			Contract.Parent = static_cast<int32>(Parent);
			Contract.LocalPositionMeters = FVector3d(
				(*Position)[0]->AsNumber(),
				(*Position)[1]->AsNumber(),
				(*Position)[2]->AsNumber());

			// rotation 是参考姿态的一部分（面部/末端骨补全需要）；老包可能没有，
			// 但一旦写了就必须是四元数，不能静默当成单位旋转。
			const TArray<TSharedPtr<FJsonValue>>* Rotation = nullptr;
			if (Bone->TryGetArrayField(TEXT("rotation"), Rotation))
			{
				if (Rotation == nullptr || Rotation->Num() != 4)
				{
					OutError = FString::Printf(
						TEXT("skeleton.json %s 的 rotation 不是四元数。"), *Name);
					return false;
				}
				Contract.LocalRotation = FQuat4d(
					(*Rotation)[0]->AsNumber(),
					(*Rotation)[1]->AsNumber(),
					(*Rotation)[2]->AsNumber(),
					(*Rotation)[3]->AsNumber()).GetNormalized();
			}
			OutBones.Add(Contract);
		}
		return true;
	}

	bool ParseDoubleArray(
		const TSharedPtr<FJsonObject>& Root,
		const TCHAR* Field,
		TArray<float>& OutValues,
		FString& OutError)
	{
		const TArray<TSharedPtr<FJsonValue>>* Values = nullptr;
		if (!Root.IsValid() || !Root->TryGetArrayField(Field, Values) || Values == nullptr)
		{
			OutError = FString::Printf(TEXT("导出包缺少 %s。"), Field);
			return false;
		}
		OutValues.Reset(Values->Num());
		for (const TSharedPtr<FJsonValue>& Value : *Values)
		{
			OutValues.Add(static_cast<float>(Value->AsNumber()));
		}
		return true;
	}

	/**
	 * manifest.json 的 timing 是导出时写死的窗口契约。
	 *
	 * 当前 C++ 装配器只实现 24/24 窗口；如果包的 timing 不是这组值，
	 * 继续按 24/24 装配会把数据切成错误的形状，因此这里直接拒绝。
	 */
	bool ParseManifestTiming(
		const FString& PackageDir,
		double& OutFrameRate,
		int32& OutHistoryFrames,
		int32& OutFutureFrames,
		FString& OutError)
	{
		FString ManifestText;
		if (!FFileHelper::LoadFileToString(
			ManifestText, *(PackageDir / TEXT("manifest.json"))))
		{
			OutError = FString::Printf(
				TEXT("无法读取 %s。"), *(PackageDir / TEXT("manifest.json")));
			return false;
		}

		TSharedPtr<FJsonObject> Manifest;
		if (!FJsonSerializer::Deserialize(
			TJsonReaderFactory<TCHAR>::Create(ManifestText), Manifest)
			|| !Manifest.IsValid())
		{
			OutError = TEXT("manifest.json 解析失败。");
			return false;
		}

		const TSharedPtr<FJsonObject>* Timing = nullptr;
		if (!Manifest->TryGetObjectField(TEXT("timing"), Timing)
			|| Timing == nullptr || !Timing->IsValid())
		{
			OutError = TEXT("manifest.json 缺少 timing。");
			return false;
		}

		double FrameRate = 0.0;
		double HistoryFrames = 0.0;
		double FutureFrames = 0.0;
		if (!(*Timing)->TryGetNumberField(TEXT("fps"), FrameRate)
			|| !(*Timing)->TryGetNumberField(TEXT("history_frames"), HistoryFrames)
			|| !(*Timing)->TryGetNumberField(TEXT("future_frames"), FutureFrames))
		{
			OutError = TEXT("manifest.json 的 timing 缺 fps/history_frames/future_frames。");
			return false;
		}

		if (!FMath::IsFinite(FrameRate) || FrameRate <= 0.0)
		{
			OutError = TEXT("manifest.json 的 timing.fps 无效。");
			return false;
		}
		if (!FMath::IsNearlyEqual(HistoryFrames, FMath::RoundToDouble(HistoryFrames))
			|| !FMath::IsNearlyEqual(FutureFrames, FMath::RoundToDouble(FutureFrames))
			|| HistoryFrames < 1.0 || FutureFrames < 1.0)
		{
			OutError = TEXT("manifest.json 的 timing 帧数不是正整数。");
			return false;
		}
		if (static_cast<int32>(HistoryFrames) != FRefPoseWindowBuilder::HistoryFrames
			|| static_cast<int32>(FutureFrames) != FRefPoseWindowBuilder::FutureFrames)
		{
			OutError = FString::Printf(
				TEXT("模型窗口为 %d/%d，但当前 C++ 装配器只支持 %d/%d。"),
				static_cast<int32>(HistoryFrames),
				static_cast<int32>(FutureFrames),
				FRefPoseWindowBuilder::HistoryFrames,
				FRefPoseWindowBuilder::FutureFrames);
			return false;
		}

		OutFrameRate = FrameRate;
		OutHistoryFrames = static_cast<int32>(HistoryFrames);
		OutFutureFrames = static_cast<int32>(FutureFrames);
		return true;
	}
}

URefPoseModelAsset* URefPoseModelAsset::CreateFromOnnxFile(const FString& OnnxPath, FString& OutError)
{
	TArray<uint8> Bytes;
	if (!FFileHelper::LoadFileToArray(Bytes, *OnnxPath) || Bytes.IsEmpty())
	{
		OutError = FString::Printf(TEXT("无法读取 ONNX：%s"), *OnnxPath);
		return nullptr;
	}

	URefPoseModelAsset* Asset = NewObject<URefPoseModelAsset>();
	Asset->ModelData = NewObject<UNNEModelData>(Asset);
	Asset->ModelData->Init(TEXT("onnx"), MakeArrayView(Bytes));
	Asset->ModelData->SetTargetRuntimes({ TEXT("NNERuntimeORTCpu") });
	return Asset;
}

bool URefPoseModelAsset::LoadContractFromPackage(const FString& PackageDir, FString& OutError)
{
	// skeleton.json：骨名、父索引、参考平移。
	FString SkeletonText;
	if (!FFileHelper::LoadFileToString(SkeletonText, *(PackageDir / TEXT("skeleton.json"))))
	{
		OutError = FString::Printf(TEXT("无法读取 %s。"), *(PackageDir / TEXT("skeleton.json")));
		return false;
	}
	TSharedPtr<FJsonObject> Skeleton;
	if (!FJsonSerializer::Deserialize(
		TJsonReaderFactory<TCHAR>::Create(SkeletonText), Skeleton) || !Skeleton.IsValid())
	{
		OutError = TEXT("skeleton.json 解析失败。");
		return false;
	}
	if (!ParseBones(Skeleton, Bones, OutError))
	{
		return false;
	}
	BoneCount = Bones.Num();

	// stats.bin：先是均值 78*3，再是标准差 78*3，全部小端 f32。
	TArray<uint8> StatsBytes;
	if (!FFileHelper::LoadFileToArray(StatsBytes, *(PackageDir / TEXT("stats.bin"))))
	{
		OutError = TEXT("无法读取 stats.bin。");
		return false;
	}
	const int32 ExpectedBytes = BoneCount * 3 * 2 * static_cast<int32>(sizeof(float));
	if (StatsBytes.Num() != ExpectedBytes)
	{
		OutError = FString::Printf(
			TEXT("stats.bin 长度应为 %d，实际 %d。"), ExpectedBytes, StatsBytes.Num());
		return false;
	}
	const int32 HalfCount = BoneCount * 3;
	PoseMean.SetNumUninitialized(HalfCount);
	PoseStd.SetNumUninitialized(HalfCount);
	FMemory::Memcpy(PoseMean.GetData(), StatsBytes.GetData(), HalfCount * sizeof(float));
	FMemory::Memcpy(
		PoseStd.GetData(), StatsBytes.GetData() + HalfCount * sizeof(float), HalfCount * sizeof(float));

	// 标准差为 0 会让反归一化除零；这里直接拒绝而不是产出 NaN 姿态。
	for (int32 Index = 0; Index < HalfCount; ++Index)
	{
		if (!FMath::IsFinite(PoseStd[Index]) || FMath::IsNearlyZero(PoseStd[Index]))
		{
			OutError = FString::Printf(TEXT("stats.bin 第 %d 个标准差无效。"), Index);
			return false;
		}
	}

	// 播放时钟与窗口长度也必须来自同一个导出包，不能靠默认值。
	if (!ParseManifestTiming(
		PackageDir, FrameRate, HistoryFrames, FutureFrames, OutError))
	{
		return false;
	}
	return true;
}

bool URefPoseModelAsset::HasUsableContract() const
{
	const int32 Expected = BoneCount * 3;
	if (BoneCount <= 0 || HistoryFrames <= 0 || FutureFrames <= 0)
	{
		return false;
	}
	if (!FMath::IsFinite(FrameRate) || FrameRate <= 0.0)
	{
		return false;
	}
	if (Bones.Num() != BoneCount)
	{
		return false;
	}
	if (PoseMean.Num() != Expected || PoseStd.Num() != Expected)
	{
		return false;
	}
	// 父先子后是装配与蒙皮的前置条件；这里不替调用方猜顺序。
	for (int32 Index = 0; Index < Bones.Num(); ++Index)
	{
		if (Bones[Index].Parent < -1 || Bones[Index].Parent >= Index)
		{
			return false;
		}
	}
	return true;
}
