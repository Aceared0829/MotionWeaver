// Copyright ZhaoZining. All Rights Reserved.

#include "RefPose/RefPoseWindowBuilder.h"

namespace
{
	/** 训练只见过这四种参考档位；其它组合属于分布外，必须显式拒绝。 */
	bool IsCoveredReferenceOffsets(TConstArrayView<int32> Offsets)
	{
		if (Offsets.Num() == 0)
		{
			return true;
		}
		if (Offsets.Num() == 1)
		{
			return Offsets[0] == 12 || Offsets[0] == 23;
		}
		if (Offsets.Num() == 2)
		{
			return Offsets[0] == 12 && Offsets[1] == 23;
		}
		return false;
	}

	FString DescribeOffsets(TConstArrayView<int32> Offsets)
	{
		FString Joined;
		for (int32 Index = 0; Index < Offsets.Num(); ++Index)
		{
			Joined += FString::Printf(TEXT("%s%d"), Index > 0 ? TEXT(",") : TEXT(""), Offsets[Index]);
		}
		return Joined.IsEmpty() ? FString(TEXT("()")) : FString::Printf(TEXT("(%s)"), *Joined);
	}

	/**
	 * 把一帧姿态写入 9 维特征：位置归一化 + 6D 旋转原样。
	 *
	 * 排布是「逐骨 9 维」而不是「先全部位置、再全部旋转」：
	 * 训练侧 build_sample 用 np.concatenate((pose, rotations), axis=-1)
	 * 后直接 reshape，实际落盘就是 bone-major interleaved。
	 * 这里若按块写，NNE 数值比对仍会通过（夹具直接灌张量），
	 * 但生成器会把位置读成别的骨、旋转读成位置，姿态整体错位。
	 */
	void WritePoseFeatures(
		const FRefPoseWindowBuilder::FPoseFrame& Pose,
		int32 BoneCount,
		const FRefPoseWindowBuilder::FNormalization& Norm,
		float* OutRow)
	{
		const float* Mean = Norm.Mean.GetData();
		const float* Std = Norm.Std.GetData();
		for (int32 Bone = 0; Bone < BoneCount; ++Bone)
		{
			float* BoneRow = OutRow + Bone * 9;
			const FVector3d& Position = Pose.PositionsMeters[Bone];
			BoneRow[0] = static_cast<float>((Position.X - Mean[Bone * 3 + 0]) / Std[Bone * 3 + 0]);
			BoneRow[1] = static_cast<float>((Position.Y - Mean[Bone * 3 + 1]) / Std[Bone * 3 + 1]);
			BoneRow[2] = static_cast<float>((Position.Z - Mean[Bone * 3 + 2]) / Std[Bone * 3 + 2]);
			FMemory::Memcpy(
				BoneRow + 3,
				Pose.Rotations6D.GetData() + Bone * 6,
				sizeof(float) * 6);
		}
	}

	/** 把一条 root_local 序列写进 7 维槽位。 */
	void WriteRootRow(const FRefPoseWindowBuilder::FRootSample& Sample, float* OutRow)
	{
		OutRow[0] = static_cast<float>(Sample.PositionMeters.X);
		OutRow[1] = static_cast<float>(Sample.PositionMeters.Y);
		OutRow[2] = static_cast<float>(Sample.PositionMeters.Z);
		OutRow[3] = static_cast<float>(Sample.Rotation.X);
		OutRow[4] = static_cast<float>(Sample.Rotation.Y);
		OutRow[5] = static_cast<float>(Sample.Rotation.Z);
		OutRow[6] = static_cast<float>(Sample.Rotation.W);
	}
}

TArray<FRefPoseWindowBuilder::FRootSample> FRefPoseWindowBuilder::RootLocal(
	TConstArrayView<FRootSample> Track,
	const FRootSample& Origin)
{
	// 位置与朝向都相对 origin。只减位置会让朝向漂移，模型看到的是分布外输入。
	const FQuat4d Inverse = Origin.Rotation.Inverse();
	TArray<FRootSample> Result;
	Result.Reserve(Track.Num());
	for (const FRootSample& Sample : Track)
	{
		FRootSample Local;
		Local.PositionMeters = Inverse.RotateVector(Sample.PositionMeters - Origin.PositionMeters);
		Local.Rotation = Inverse * Sample.Rotation;
		Result.Add(Local);
	}
	return Result;
}

bool FRefPoseWindowBuilder::Build(
	int32 BoneCount,
	TConstArrayView<FPoseFrame> History,
	TConstArrayView<FRootSample> HistoryRoot,
	TConstArrayView<FPoseFrame> Future,
	const FRootSample& RootOrigin,
	TConstArrayView<FRootSample> FutureRoot,
	TConstArrayView<int32> ReferenceOffsets,
	const FNormalization& Norm,
	FWindow& OutWindow,
	FString& OutError)
{
	if (BoneCount <= 0)
	{
		OutError = TEXT("RefPose 装配：骨数无效。");
		return false;
	}
	if (!Norm.IsValid(BoneCount))
	{
		OutError = TEXT("RefPose 装配：归一化统计量长度与骨数不符。");
		return false;
	}
	if (History.Num() != HistoryFrames)
	{
		OutError = FString::Printf(
			TEXT("RefPose 装配：历史应为 %d 帧，实际 %d。"), HistoryFrames, History.Num());
		return false;
	}
	if (HistoryRoot.Num() != HistoryFrames)
	{
		OutError = FString::Printf(
			TEXT("RefPose 装配：历史 Root 应为 %d 帧，实际 %d。"), HistoryFrames, HistoryRoot.Num());
		return false;
	}
	if (Future.Num() != FutureFrames)
	{
		OutError = FString::Printf(
			TEXT("RefPose 装配：未来应为 %d 帧，实际 %d。"), FutureFrames, Future.Num());
		return false;
	}
	if (FutureRoot.Num() != FutureFrames)
	{
		OutError = FString::Printf(
			TEXT("RefPose 装配：未来 Root 应为 %d 帧，实际 %d。"), FutureFrames, FutureRoot.Num());
		return false;
	}
	if (!IsCoveredReferenceOffsets(ReferenceOffsets))
	{
		OutError = FString::Printf(
			TEXT("RefPose 装配：参考档位 %s 不在已验证集合 ()/(12)/(23)/(12,23) 内。"),
			*DescribeOffsets(ReferenceOffsets));
		return false;
	}

	const int32 Features = BoneCount * 9;
	const int32 HistoryStride = Features + 7;

	OutWindow = FWindow();
	OutWindow.History.SetNumZeroed(HistoryFrames * HistoryStride);
	OutWindow.RootPlan.SetNumZeroed(FutureFrames * 7);
	OutWindow.Reference.SetNumZeroed(FutureFrames * Features);
	OutWindow.Mask.SetNumZeroed(FutureFrames);
	OutWindow.Target.SetNumZeroed(FutureFrames * Features);

	// 历史：姿态特征 + 同一条轨迹上的 root_local。两者必须同源，
	// 用别处的 Root 拼历史会得到「姿态与位移不匹配」的滑步输入。
	const TArray<FRootSample> LocalHistory = RootLocal(HistoryRoot, RootOrigin);
	for (int32 Frame = 0; Frame < HistoryFrames; ++Frame)
	{
		const FPoseFrame& Pose = History[Frame];
		if (Pose.PositionsMeters.Num() != BoneCount || Pose.Rotations6D.Num() != BoneCount * 6)
		{
			OutError = FString::Printf(TEXT("RefPose 装配：历史帧 %d 的骨骼数据长度不符。"), Frame);
			return false;
		}
		float* Row = OutWindow.History.GetData() + Frame * HistoryStride;
		WritePoseFeatures(Pose, BoneCount, Norm, Row);
		WriteRootRow(LocalHistory[Frame], Row + Features);
	}

	// 未来：target 与 root_plan。
	for (int32 Frame = 0; Frame < FutureFrames; ++Frame)
	{
		const FPoseFrame& Pose = Future[Frame];
		if (Pose.PositionsMeters.Num() != BoneCount || Pose.Rotations6D.Num() != BoneCount * 6)
		{
			OutError = FString::Printf(TEXT("RefPose 装配：未来帧 %d 的骨骼数据长度不符。"), Frame);
			return false;
		}
		WritePoseFeatures(
			Pose, BoneCount, Norm, OutWindow.Target.GetData() + Frame * Features);
	}

	const TArray<FRootSample> LocalFuture = RootLocal(FutureRoot, RootOrigin);
	for (int32 Frame = 0; Frame < FutureFrames; ++Frame)
	{
		WriteRootRow(LocalFuture[Frame], OutWindow.RootPlan.GetData() + Frame * 7);
	}

	// mask 是硬覆盖；未选中帧的 reference 必须整行为 0，不是重复值也不是 NaN。
	for (const int32 Offset : ReferenceOffsets)
	{
		if (Offset < 0 || Offset >= FutureFrames)
		{
			OutError = FString::Printf(TEXT("RefPose 装配：参考下标 %d 越界。"), Offset);
			return false;
		}
		OutWindow.Mask[Offset] = 1.0f;
		FMemory::Memcpy(
			OutWindow.Reference.GetData() + Offset * Features,
			OutWindow.Target.GetData() + Offset * Features,
			sizeof(float) * Features);
	}

	if (!OutWindow.IsSized(BoneCount))
	{
		OutError = TEXT("RefPose 装配：输出长度与契约不符。");
		return false;
	}
	return true;
}
