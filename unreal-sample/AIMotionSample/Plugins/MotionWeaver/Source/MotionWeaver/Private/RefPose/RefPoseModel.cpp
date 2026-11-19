// Copyright ZhaoZining. All Rights Reserved.

#include "RefPose/RefPoseModel.h"

#include "HAL/FileManager.h"
#include "Misc/FileHelper.h"
#include "NNEModelData.h"
#include "RefPose/RefPosePoseContract.h"
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

	/**
	 * 从 fixtures 统计旋转通道的稳定先验。
	 *
	 * fixtures 是真实训练窗口（history 24 × (78*9+7)）。这里把所有 fixture
	 * 帧的逐骨 6D 旋转按列求平均，再对两列做一次 Gram–Schmidt，得到每根骨
	 * 一个正交的 6D 先验。它不是「某个姿态」，而是训练分布的中心朝向；
	 * 用途见 URefPoseModelAsset::RotationPrior6D 的注释。
	 *
	 * 包没有 fixtures 时返回 false，调用方退回逐帧 UE 旋转（并明确告警）。
	 */
	bool LoadRotationPrior(
		const FString& PackageDir,
		int32 BoneCount,
		int32 HistoryFrames,
		TConstArrayView<FRefPoseBoneContract> Bones,
		TConstArrayView<float> Mean,
		TConstArrayView<float> Std,
		TArray<float>& OutPrior,
		TArray<FVector3d>& OutAxis,
		TArray<int32>& OutChild,
		FString& OutError)
	{
		const FString FixtureRoot = PackageDir / TEXT("fixtures");
		TArray<FString> FixtureFiles;
		// 注意：IFileManager::FindFiles 只支持「文件名」部分的通配符，
		// `fixtures/*/history.f32` 这种目录段带通配的写法永远匹配不到文件。
		// 之前正是因此让先验静默加载失败，历史旋转退回 UE 旋转通道，
		// 模型输入与训练骨轴不同源，播放出来整个人是塌的。
		IFileManager::Get().FindFilesRecursive(
			FixtureFiles, *FixtureRoot, TEXT("history.f32"), true, false);
		if (FixtureFiles.Num() == 0)
		{
			OutError = TEXT("模型包没有 fixtures/*/history.f32，无法建立旋转先验。");
			return false;
		}

		const int32 Stride = BoneCount * FRefPosePoseContract::BoneStride + 7;
		const int32 ExpectedFloats = HistoryFrames * Stride;

		// 累积每根骨 6D 的前两列；列内偏移与 Decode6D 一致：
		// col0 = (v0, v2, v4)，col1 = (v1, v3, v5)。
		TArray<double> SumCol0;
		TArray<double> SumCol1;
		SumCol0.SetNumZeroed(BoneCount * 3);
		SumCol1.SetNumZeroed(BoneCount * 3);
		// 每根骨的每个子骨一个累加器：统计 R^T·d 的平均方向与集中度。
		TArray<TArray<FVector3d>> ChildDirectionSums;
		TArray<TArray<int32>> ChildDirectionCounts;
		ChildDirectionSums.SetNum(BoneCount);
		ChildDirectionCounts.SetNum(BoneCount);
		for (int32 Bone = 0; Bone < BoneCount; ++Bone)
		{
			for (int32 Child = 0; Child < BoneCount; ++Child)
			{
				if (Bones[Child].Parent == Bone)
				{
					ChildDirectionSums[Bone].Add(FVector3d::ZeroVector);
					ChildDirectionCounts[Bone].Add(0);
				}
			}
		}
		int32 SampleCount = 0;

		for (const FString& FileName : FixtureFiles)
		{
			TArray<uint8> Bytes;
			if (!FFileHelper::LoadFileToArray(Bytes, *FileName))
			{
				continue;
			}
			if (Bytes.Num() != ExpectedFloats * static_cast<int32>(sizeof(float)))
			{
				OutError = FString::Printf(
					TEXT("fixture %s 长度应为 %d 个 float，实际 %d 字节。"),
					*FileName, ExpectedFloats, Bytes.Num());
				return false;
			}
			const float* Values = reinterpret_cast<const float*>(Bytes.GetData());
			for (int32 Frame = 0; Frame < HistoryFrames; ++Frame)
			{
				const float* FrameBase = Values + static_cast<int64>(Frame) * Stride;
				TArray<FVector3d> Positions;
				Positions.SetNumUninitialized(BoneCount);
				for (int32 Bone = 0; Bone < BoneCount; ++Bone)
				{
					const float* BoneBase = FrameBase
						+ static_cast<int64>(Bone) * FRefPosePoseContract::BoneStride
						+ FRefPosePoseContract::PositionStride;
					const int32 Base = Bone * 3;
					SumCol0[Base + 0] += BoneBase[0];
					SumCol0[Base + 1] += BoneBase[2];
					SumCol0[Base + 2] += BoneBase[4];
					SumCol1[Base + 0] += BoneBase[1];
					SumCol1[Base + 1] += BoneBase[3];
					SumCol1[Base + 2] += BoneBase[5];
					const float* PositionBase = FrameBase
						+ static_cast<int64>(Bone) * FRefPosePoseContract::BoneStride;
					Positions[Bone] = FVector3d(
						PositionBase[0] * Std[Base + 0] + Mean[Base + 0],
						PositionBase[1] * Std[Base + 1] + Mean[Base + 1],
						PositionBase[2] * Std[Base + 2] + Mean[Base + 2]);
				}

				// 逐骨求 R^T·d：R 的两列已在上面的 Sum 里，但这里需要逐帧值，
				// 因此直接从 6D 解码。d 是「骨 → 子骨」的单位方向。
				for (int32 Bone = 0; Bone < BoneCount; ++Bone)
				{
					if (ChildDirectionSums[Bone].Num() == 0)
					{
						continue;
					}
					const float* RotationBase = FrameBase
						+ static_cast<int64>(Bone) * FRefPosePoseContract::BoneStride
						+ FRefPosePoseContract::PositionStride;
					const FQuat4d Rotation = FRefPosePoseContract::Decode6D(
						MakeArrayView(RotationBase, FRefPosePoseContract::RotationStride));
					int32 ChildSlot = 0;
					for (int32 Child = 0; Child < BoneCount; ++Child)
					{
						if (Bones[Child].Parent != Bone)
						{
							continue;
						}
						const FVector3d Direction =
							(Positions[Child] - Positions[Bone]).GetSafeNormal();
						if (!Direction.IsNearlyZero())
						{
							ChildDirectionSums[Bone][ChildSlot] +=
								Rotation.Inverse().RotateVector(Direction);
							++ChildDirectionCounts[Bone][ChildSlot];
						}
						++ChildSlot;
					}
				}
			}
			SampleCount += HistoryFrames;
		}

		if (SampleCount == 0)
		{
			OutError = TEXT("fixtures 里没有可读的 history.f32。");
			return false;
		}

		OutPrior.SetNumUninitialized(BoneCount * FRefPosePoseContract::RotationStride);
		for (int32 Bone = 0; Bone < BoneCount; ++Bone)
		{
			const int32 Base = Bone * 3;
			FVector3d Col0(
				SumCol0[Base + 0] / SampleCount,
				SumCol0[Base + 1] / SampleCount,
				SumCol0[Base + 2] / SampleCount);
			FVector3d Col1(
				SumCol1[Base + 0] / SampleCount,
				SumCol1[Base + 1] / SampleCount,
				SumCol1[Base + 2] / SampleCount);
			if (!Col0.Normalize() || !Col1.Normalize())
			{
				OutError = FString::Printf(
					TEXT("fixture 第 %d 根骨的平均旋转列退化为零。"), Bone);
				return false;
			}
			Col1 = (Col1 - Col0 * (Col1 | Col0)).GetSafeNormal();
			if (Col1.IsNearlyZero())
			{
				OutError = FString::Printf(
					TEXT("fixture 第 %d 根骨的平均旋转两列共线。"), Bone);
				return false;
			}

			float* Out = OutPrior.GetData() + Bone * FRefPosePoseContract::RotationStride;
			Out[0] = static_cast<float>(Col0.X);
			Out[2] = static_cast<float>(Col0.Y);
			Out[4] = static_cast<float>(Col0.Z);
			Out[1] = static_cast<float>(Col1.X);
			Out[3] = static_cast<float>(Col1.Y);
			Out[5] = static_cast<float>(Col1.Z);
		}

		// 选「旋转坐标系里最稳定」的子骨作为该骨的朝向锚点。
		// 阈值用平均合矢量长度：接近 1 表示该方向在整段训练数据里是常量。
		OutAxis.SetNum(BoneCount);
		OutChild.Init(INDEX_NONE, BoneCount);
		for (int32 Bone = 0; Bone < BoneCount; ++Bone)
		{
			OutAxis[Bone] = FVector3d::ZeroVector;
			if (ChildDirectionSums[Bone].Num() == 0)
			{
				continue;
			}
			int32 BestSlot = INDEX_NONE;
			double BestConcentration = 0.0;
			FVector3d BestMean = FVector3d::ZeroVector;
			int32 ChildSlot = 0;
			for (int32 Child = 0; Child < BoneCount; ++Child)
			{
				if (Bones[Child].Parent != Bone)
				{
					continue;
				}
				if (ChildDirectionCounts[Bone][ChildSlot] > 0)
				{
					const FVector3d MeanDirection =
						ChildDirectionSums[Bone][ChildSlot]
						/ static_cast<double>(ChildDirectionCounts[Bone][ChildSlot]);
					const double Concentration = MeanDirection.Size();
					if (Concentration > BestConcentration)
					{
						BestConcentration = Concentration;
						BestSlot = ChildSlot;
						BestMean = MeanDirection.GetSafeNormal();
					}
				}
				++ChildSlot;
			}
			if (BestSlot != INDEX_NONE && BestConcentration >= 0.2)
			{
				int32 FoundChild = INDEX_NONE;
				int32 ScanSlot = 0;
				for (int32 Child = 0; Child < BoneCount; ++Child)
				{
					if (Bones[Child].Parent != Bone)
					{
						continue;
					}
					if (ScanSlot == BestSlot)
					{
						FoundChild = Child;
						break;
					}
					++ScanSlot;
				}
				OutAxis[Bone] = BestMean;
				OutChild[Bone] = FoundChild;
			}
		}
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

	// 旋转先验是可选增强：老包没有 fixtures 时保持为空，装配器会退回
	// 逐帧 UE 旋转并告警。不把「缺 fixtures」当成加载失败。
	FString PriorError;
	if (!LoadRotationPrior(
		PackageDir, BoneCount, HistoryFrames, Bones, PoseMean, PoseStd,
		RotationPrior6D, RotationAxis, RotationChild, PriorError))
	{
		RotationPrior6D.Reset();
		RotationAxis.Reset();
		RotationChild.Reset();
		UE_LOG(LogTemp, Warning,
			TEXT("[RefPose] 未建立旋转先验：%s"), *PriorError);
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

bool URefPoseModelAsset::HasRotationCalibration() const
{
	return BoneCount > 0
		&& RotationAxis.Num() == BoneCount
		&& RotationChild.Num() == BoneCount;
}

bool URefPoseModelAsset::EncodeRotationsFromPositions(
	TConstArrayView<FVector3d> PositionsMeters,
	TArray<float>& OutRotations6D) const
{
	if (!HasRotationCalibration() || PositionsMeters.Num() != BoneCount)
	{
		return false;
	}

	OutRotations6D.SetNumUninitialized(BoneCount * FRefPosePoseContract::RotationStride);
	TArray<FQuat4d> GlobalRotations;
	GlobalRotations.SetNumUninitialized(BoneCount);
	for (int32 Bone = 0; Bone < BoneCount; ++Bone)
	{
		FQuat4d Rotation = FQuat4d::Identity;
		if (Bone > 0)
		{
			const int32 Child = RotationChild[Bone];
			if (Child != INDEX_NONE && !RotationAxis[Bone].IsNearlyZero())
			{
				const FVector3d Direction =
					(PositionsMeters[Child] - PositionsMeters[Bone]).GetSafeNormal();
				if (!Direction.IsNearlyZero())
				{
					Rotation = FRefPosePoseContract::SwingBetween(
						RotationAxis[Bone], Direction);
				}
			}
			if (Rotation.IsIdentity())
			{
				// 叶子骨（无子骨）没有可观测的朝向，沿用父骨；这与训练数据里
				// End 骨跟随父骨的统计行为一致，也避免写出一条全零 6D。
				const int32 Parent = Bones[Bone].Parent;
				Rotation = Parent >= 0 ? GlobalRotations[Parent] : FQuat4d::Identity;
			}
		}
		GlobalRotations[Bone] = Rotation;
		FRefPosePoseContract::Encode6D(
			Rotation,
			OutRotations6D.GetData() + Bone * FRefPosePoseContract::RotationStride);
	}
	return true;
}
