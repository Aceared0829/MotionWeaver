// Copyright ZhaoZining. All Rights Reserved.

#include "RefPose/RefPoseGenerator.h"

#include "Pose/Data/PoseTypes.h"
#include "RefPose/RefPoseModel.h"
#include "RefPose/RefPoseSession.h"

bool FRefPoseGenerator::GenerateWindow(
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
	FString& OutError)
{
	OutMetrics = FRefPoseGenerationMetrics();
	if (!Model.HasUsableContract())
	{
		OutError = TEXT("模型缺少骨架契约或统计量，无法装配输入。");
		return false;
	}
	const int32 BoneCount = Model.BoneCount;

	// 1) 校验父索引并可复用地取出。
	TArray<int32> Parents;
	Parents.SetNumUninitialized(BoneCount);
	for (int32 Index = 0; Index < BoneCount; ++Index)
	{
		Parents[Index] = Model.Bones[Index].Parent;
	}
	if (RefLocal.Num() != BoneCount)
	{
		OutError = FString::Printf(
			TEXT("参考局部变换数应为 %d，实际 %d。"), BoneCount, RefLocal.Num());
		return false;
	}

	// 2) 组装四输入。
	FRefPoseWindowBuilder::FNormalization Norm;
	Norm.Mean = Model.PoseMean;
	Norm.Std = Model.PoseStd;

	TArray<FRefPoseWindowBuilder::FPoseFrame> HistoryFrames;
	HistoryFrames.Reserve(History.Num());
	for (const FPoseInput& Input : History)
	{
		FRefPoseWindowBuilder::FPoseFrame Frame;
		Frame.PositionsMeters = Input.PositionsMeters;
		Frame.Rotations6D = Input.Rotations6D;
		HistoryFrames.Add(MoveTemp(Frame));
	}
	TArray<FRefPoseWindowBuilder::FPoseFrame> FutureFrames;
	FutureFrames.Reserve(Future.Num());
	for (const FPoseInput& Input : Future)
	{
		FRefPoseWindowBuilder::FPoseFrame Frame;
		Frame.PositionsMeters = Input.PositionsMeters;
		Frame.Rotations6D = Input.Rotations6D;
		FutureFrames.Add(MoveTemp(Frame));
	}

	FRefPoseWindowBuilder::FWindow Window;
	if (!FRefPoseWindowBuilder::Build(
		BoneCount, HistoryFrames, HistoryRoot, FutureFrames, RootOrigin, FutureRoot,
		ReferenceOffsets, Norm, Window, OutError))
	{
		return false;
	}

	// 3) 推理。
	FRefPoseInputTensors Tensors;
	Tensors.History = Window.History;
	Tensors.RootPlan = Window.RootPlan;
	Tensors.Reference = Window.Reference;
	Tensors.Mask = Window.Mask;

	TArray<float> Joints;
	double Milliseconds = 0.0;
	if (!Session.Run(Tensors, Joints, Milliseconds, OutError))
	{
		return false;
	}
	OutMetrics.TotalInferenceMs += Milliseconds;
	OutMetrics.MaxInferenceMs = FMath::Max(OutMetrics.MaxInferenceMs, Milliseconds);
	OutMetrics.WindowCount = 1;

	// 4) 解码 + 解算。
	TArray<FRefPosePoseContract::FDecodedFrame> Decoded;
	if (!FRefPosePoseContract::DecodeWindow(
		Joints, BoneCount, Model.FutureFrames, Model.PoseMean, Model.PoseStd, Decoded, OutError))
	{
		return false;
	}

	TArray<TArray<FTransform>> Local;
	FRefPoseSkeletonSolver::FMetrics SolverMetrics;
	if (!FRefPoseSkeletonSolver::Solve(Parents, RefLocal, Decoded, Local, SolverMetrics, OutError))
	{
		return false;
	}

	OutMetrics.MaxBoneLengthDeviationCm = SolverMetrics.MaxBoneLengthDeviationCm;
	OutMetrics.DegenerateBones += SolverMetrics.DegenerateBones;
	OutMetrics.FrameCount = Local.Num();
	for (const int32 Offset : ReferenceOffsets)
	{
		if (Offset >= 0 && Offset < Model.FutureFrames)
		{
			++OutMetrics.HardReferenceFrames;
		}
	}

	OutFrames.Local = MoveTemp(Local);
	return true;
}

bool FRefPoseGenerator::GenerateWindow(
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
	FString& OutError)
{
	TSharedPtr<FRefPoseSession> Session = FRefPoseSession::Create(Model, OutError);
	if (!Session.IsValid())
	{
		return false;
	}
	return GenerateWindow(
		Model, *Session, History, HistoryRoot, Future, RootOrigin, FutureRoot,
		ReferenceOffsets, RefLocal, OutFrames, OutMetrics, OutError);
}

bool FRefPoseGenerator::BuildSolverReference(
	const URefPoseModelAsset& Model,
	const FRefPoseSkeletonMap& Map,
	TConstArrayView<FTransform> SkeletonReference,
	TConstArrayView<int32> SkeletonParents,
	TArray<FTransform>& OutReference,
	FString& OutError)
{
	const int32 ModelCount = Model.Bones.Num();
	const int32 SkeletonCount = SkeletonParents.Num();
	if (ModelCount <= 0 || SkeletonCount <= 0)
	{
		OutError = TEXT("求解器参考：模型或骨架没有骨骼。");
		return false;
	}
	if (SkeletonReference.Num() != SkeletonCount
		|| Map.ModelToSkeleton.Num() != ModelCount
		|| Map.SkeletonToModel.Num() != SkeletonCount)
	{
		OutError = TEXT("求解器参考：模型/骨架/映射的骨数不一致。");
		return false;
	}

	// UE 参考骨架的全局变换：父先子后逐级累乘。
	TArray<FTransform> SkeletonGlobal;
	SkeletonGlobal.SetNumUninitialized(SkeletonCount);
	for (int32 Index = 0; Index < SkeletonCount; ++Index)
	{
		const int32 Parent = SkeletonParents[Index];
		if (Parent < -1 || Parent >= Index)
		{
			OutError = FString::Printf(
				TEXT("求解器参考：UE 骨 %d 的父索引 %d 非法。"), Index, Parent);
			return false;
		}
		SkeletonGlobal[Index] = Parent < 0
			? SkeletonReference[Index]
			: SkeletonGlobal[Parent] * SkeletonReference[Index];
	}

	OutReference.Reset(ModelCount);
	for (int32 ModelIndex = 0; ModelIndex < ModelCount; ++ModelIndex)
	{
		const FRefPoseBoneContract& Bone = Model.Bones[ModelIndex];
		if (Bone.Parent < -1 || Bone.Parent >= ModelIndex)
		{
			OutError = FString::Printf(
				TEXT("求解器参考：模型骨 %d 的父索引 %d 非法。"), ModelIndex, Bone.Parent);
			return false;
		}

		FQuat4d LocalRotation = FQuat4d::Identity;
		const int32 SkeletonIndex = Map.ModelToSkeleton[ModelIndex];
		if (Bone.Parent >= 0 && SkeletonIndex != INDEX_NONE)
		{
			// 模型父骨映射到 UE 的最近映射祖先；中间可能隔着 spine_04/spine_05
			// 这类 UE 独有骨。求解器的局部旋转是相对这个祖先表达的，
			// 与 Retarget 里 ChainInverse 的语义严格对应。
			int32 Ancestor = SkeletonParents[SkeletonIndex];
			while (Ancestor >= 0 && Map.SkeletonToModel[Ancestor] == INDEX_NONE)
			{
				Ancestor = SkeletonParents[Ancestor];
			}
			if (Ancestor < 0 || Map.SkeletonToModel[Ancestor] != Bone.Parent)
			{
				OutError = FString::Printf(
					TEXT("求解器参考：模型骨 %s 的 UE 最近映射祖先与模型父骨不一致。"),
					*Bone.Name.ToString());
				return false;
			}
			const FQuat4d AncestorRotation =
				FQuat4d(SkeletonGlobal[Ancestor].GetRotation()).GetNormalized();
			const FQuat4d BoneRotation =
				FQuat4d(SkeletonGlobal[SkeletonIndex].GetRotation()).GetNormalized();
			LocalRotation = (AncestorRotation.Inverse() * BoneRotation).GetNormalized();
		}

		// 长度必须保留模型契约的骨长：求解器用它构造中间变换，
		// 最终长度由 Retarget 统一换成 UE 参考骨长。
		OutReference.Add(FTransform(
			LocalRotation, FVector(Bone.LocalPositionMeters * 100.0)));
	}
	return true;
}

bool FRefPoseGenerator::SkeletonPoseToPoseInput(
	const URefPoseModelAsset& Model,
	const FRefPoseSkeletonMap& Map,
	TConstArrayView<FTransform> SkeletonLocal,
	TConstArrayView<int32> SkeletonParents,
	const FRefPoseWindowBuilder::FRootSample& RootSample,
	FPoseInput& OutPose,
	FString& OutError)
{
	const int32 ModelCount = Model.Bones.Num();
	const int32 SkeletonCount = SkeletonLocal.Num();
	if (ModelCount <= 0 || SkeletonCount <= 0)
	{
		OutError = TEXT("关键帧姿态转换：模型或骨架没有骨骼。");
		return false;
	}
	if (SkeletonParents.Num() != SkeletonCount
		|| Map.ModelToSkeleton.Num() != ModelCount
		|| Map.SkeletonToModel.Num() != SkeletonCount)
	{
		OutError = TEXT("关键帧姿态转换：模型/骨架/映射的骨数不一致。");
		return false;
	}
	if (Model.Bones[0].Parent != -1)
	{
		OutError = TEXT("关键帧姿态转换：模型槽位 0 不是根骨。");
		return false;
	}

	// UE 参考骨架的全局变换。位置必须是组件空间，才能做根骨相对化。
	TArray<FTransform> Global;
	Global.SetNumUninitialized(SkeletonCount);
	int32 SkeletonRoot = INDEX_NONE;
	for (int32 Index = 0; Index < SkeletonCount; ++Index)
	{
		const int32 Parent = SkeletonParents[Index];
		if (Parent < -1 || Parent >= Index)
		{
			OutError = FString::Printf(
				TEXT("关键帧姿态转换：UE 骨 %d 的父索引 %d 非法。"), Index, Parent);
			return false;
		}
		Global[Index] = Parent < 0
			? SkeletonLocal[Index]
			: Global[Parent] * SkeletonLocal[Index];
		if (Parent < 0)
		{
			if (SkeletonRoot != INDEX_NONE)
			{
				OutError = TEXT("关键帧姿态转换：UE 骨架有多个根骨。");
				return false;
			}
			SkeletonRoot = Index;
		}
	}
	if (SkeletonRoot == INDEX_NONE)
	{
		OutError = TEXT("关键帧姿态转换：UE 骨架没有根骨。");
		return false;
	}

	// 姿态先去掉 UE 骨架自己的根变换：root motion 的唯一来源是姿态组轨迹，
	// 不能把快照里可能残留的根位移/朝向再叠一次。
	const FQuat4d RootRotationInv =
		FQuat4d(Global[SkeletonRoot].GetRotation()).GetNormalized().Inverse();
	const FVector3d RootLocation(Global[SkeletonRoot].GetTranslation());

	const FQuat4d TrackRotation = RootSample.Rotation.GetNormalized();
	const FQuat4d TrackRotationInv = TrackRotation.Inverse();

	TArray<FVector3d> LocalPositions;
	TArray<FQuat4d> LocalRotations;
	TArray<bool> bResolved;
	LocalPositions.SetNumZeroed(ModelCount);
	LocalRotations.SetNum(ModelCount);
	bResolved.Init(false, ModelCount);

	// Root 是独立辅助轨道：世界位置固定在原点，局部位置 = -R(k)ᵀ·k_pos。
	// 旋转按模型契约恒为单位四元数。
	LocalPositions[0] = TrackRotationInv.RotateVector(-RootSample.PositionMeters);
	LocalRotations[0] = FQuat4d::Identity;
	bResolved[0] = true;

	for (int32 ModelIndex = 1; ModelIndex < ModelCount; ++ModelIndex)
	{
		const int32 SkeletonIndex = Map.ModelToSkeleton[ModelIndex];
		if (SkeletonIndex == INDEX_NONE)
		{
			continue;
		}

		// 组件空间 → 去掉 UE 根骨 → 米制 Y-up → 姿态组 Root 局部帧。
		const FVector RelativeComponentPosition =
			RootRotationInv.RotateVector(
				Global[SkeletonIndex].GetTranslation() - RootLocation);
		const FQuat4d RelativeComponentRotation = (
			RootRotationInv
			* FQuat4d(Global[SkeletonIndex].GetRotation()).GetNormalized()).GetNormalized();

		const FVector3d MotionPosition =
			FRefPosePoseContract::ToMotionPosition(RelativeComponentPosition);
		const FQuat4d MotionRotation =
			FRefPosePoseContract::ToMotionRotation(RelativeComponentRotation);

		LocalPositions[ModelIndex] =
			TrackRotationInv.RotateVector(MotionPosition - RootSample.PositionMeters);
		LocalRotations[ModelIndex] =
			(TrackRotationInv * MotionRotation).GetNormalized();
		bResolved[ModelIndex] = true;
	}

	// 未映射骨（面部、手指末端、脚趾末端）不能留成零位移：
	// 从最近的已映射祖先出发，按 skeleton.json 的参考局部变换挂上去。
	// 这些骨不参与 Retarget，只影响模型的历史/参考输入。
	TArray<int32> Anchor;
	TArray<FTransform> AnchorToBone;
	Anchor.SetNum(ModelCount);
	AnchorToBone.SetNum(ModelCount);
	Anchor[0] = 0;
	AnchorToBone[0] = FTransform::Identity;
	for (int32 ModelIndex = 1; ModelIndex < ModelCount; ++ModelIndex)
	{
		if (bResolved[ModelIndex])
		{
			Anchor[ModelIndex] = ModelIndex;
			AnchorToBone[ModelIndex] = FTransform::Identity;
			continue;
		}

		const int32 Parent = Model.Bones[ModelIndex].Parent;
		if (Parent < 0 || Parent >= ModelIndex || !bResolved[Parent])
		{
			OutError = FString::Printf(
				TEXT("关键帧姿态转换：未映射骨 %s 没有可用的父骨。"),
				*Model.Bones[ModelIndex].Name.ToString());
			return false;
		}

		const FTransform LocalReference(
			Model.Bones[ModelIndex].LocalRotation.GetNormalized(),
			FVector(Model.Bones[ModelIndex].LocalPositionMeters));
		Anchor[ModelIndex] = Anchor[Parent];
		AnchorToBone[ModelIndex] = AnchorToBone[Parent] * LocalReference;

		const int32 AnchorIndex = Anchor[ModelIndex];
		LocalPositions[ModelIndex] = LocalPositions[AnchorIndex]
			+ LocalRotations[AnchorIndex].RotateVector(
				FVector3d(AnchorToBone[ModelIndex].GetTranslation()));
		LocalRotations[ModelIndex] = (
			LocalRotations[AnchorIndex]
			* FQuat4d(AnchorToBone[ModelIndex].GetRotation()).GetNormalized()).GetNormalized();
		bResolved[ModelIndex] = true;
	}

	for (int32 ModelIndex = 0; ModelIndex < ModelCount; ++ModelIndex)
	{
		if (!bResolved[ModelIndex])
		{
			OutError = FString::Printf(
				TEXT("关键帧姿态转换：模型骨 %s 没有解析出姿态。"),
				*Model.Bones[ModelIndex].Name.ToString());
			return false;
		}
		if (LocalPositions[ModelIndex].ContainsNaN())
		{
			OutError = FString::Printf(
				TEXT("关键帧姿态转换：模型骨 %s 的位置含 NaN。"),
				*Model.Bones[ModelIndex].Name.ToString());
			return false;
		}
	}

	OutPose.PositionsMeters = MoveTemp(LocalPositions);
	OutPose.Rotations6D.SetNumUninitialized(ModelCount * 6);
	for (int32 ModelIndex = 0; ModelIndex < ModelCount; ++ModelIndex)
	{
		FRefPosePoseContract::Encode6D(
			LocalRotations[ModelIndex],
			OutPose.Rotations6D.GetData() + ModelIndex * 6);
	}
	return true;
}

bool FRefPoseGenerator::SnapshotToSkeletonLocal(
	const FPoseFrameSnapshot& Snapshot,
	TConstArrayView<FName> SkeletonBoneNames,
	TConstArrayView<FTransform> SkeletonReference,
	TArray<FTransform>& OutLocal,
	FString& OutError)
{
	if (!Snapshot.IsConsistent() || Snapshot.BoneNames.Num() == 0)
	{
		OutError = TEXT("关键帧快照的骨名与变换数量不一致或为空。");
		return false;
	}
	if (SkeletonBoneNames.Num() != SkeletonReference.Num() || SkeletonBoneNames.Num() == 0)
	{
		OutError = TEXT("关键帧快照解析：UE 参考骨架的骨名与参考变换数量不符。");
		return false;
	}

	// 按名字建立索引。快照槽位顺序可能来自不同资产的保存顺序，不能按下标硬对。
	TMap<FName, int32> NameToIndex;
	NameToIndex.Reserve(SkeletonBoneNames.Num());
	for (int32 Index = 0; Index < SkeletonBoneNames.Num(); ++Index)
	{
		NameToIndex.Add(SkeletonBoneNames[Index], Index);
	}

	OutLocal = SkeletonReference;
	for (int32 Slot = 0; Slot < Snapshot.BoneNames.Num(); ++Slot)
	{
		const FName Name = Snapshot.BoneNames[Slot];
		const int32* Index = NameToIndex.Find(Name);
		if (Index == nullptr)
		{
			OutError = FString::Printf(
				TEXT("关键帧快照的骨 %s 不在 UE 参考骨架里。"), *Name.ToString());
			return false;
		}
		OutLocal[*Index] = Snapshot.LocalTransforms[Slot];
	}
	return true;
}

bool FRefPoseGenerator::BlendSkeletonLocal(
	TConstArrayView<FTransform> A,
	TConstArrayView<FTransform> B,
	double Alpha,
	TArray<FTransform>& OutLocal,
	FString& OutError)
{
	if (A.Num() != B.Num() || A.Num() == 0)
	{
		OutError = TEXT("关键帧插值：两个姿态的骨数不一致或为空。");
		return false;
	}

	const double T = FMath::Clamp(Alpha, 0.0, 1.0);
	OutLocal.Reset(A.Num());
	for (int32 Index = 0; Index < A.Num(); ++Index)
	{
		const FQuat Rotation = FQuat::Slerp(
			A[Index].GetRotation(), B[Index].GetRotation(), T);
		const FVector Translation = FMath::Lerp(
			A[Index].GetTranslation(), B[Index].GetTranslation(), T);
		const FVector Scale = FMath::Lerp(
			A[Index].GetScale3D(), B[Index].GetScale3D(), T);
		OutLocal.Add(FTransform(Rotation, Translation, Scale));
	}
	return true;
}
