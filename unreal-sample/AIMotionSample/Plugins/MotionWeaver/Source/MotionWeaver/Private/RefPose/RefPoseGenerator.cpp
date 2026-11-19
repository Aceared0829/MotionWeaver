// Copyright ZhaoZining. All Rights Reserved.

#include "RefPose/RefPoseGenerator.h"

#include "Pose/Data/PoseTypes.h"
#include "RefPose/RefPoseModel.h"
#include "RefPose/RefPoseSession.h"

#include "HAL/PlatformMisc.h"
#include "HAL/FileManager.h"
#include "Misc/FileHelper.h"

namespace
{
	/**
	 * 临时取证转储：把真实窗口的四输入、模型原始输出与模型姿态的逐骨位置
	 * 写到 MW_REFPOSE_DUMP_DIR。只在排查「畸形」时使用，默认不写盘。
	 */
	void DumpWindowForDiagnosis(
		const URefPoseModelAsset& Model,
		const FRefPoseWindowBuilder::FWindow& Window,
		TConstArrayView<float> Joints,
		TConstArrayView<FRefPosePoseContract::FDecodedFrame> Decoded,
		TConstArrayView<FRefPoseGenerator::FPoseInput> Future,
		TConstArrayView<int32> ReferenceOffsets,
		TConstArrayView<TArray<FTransform>> Local)
	{
		const FString Directory =
			FPlatformMisc::GetEnvironmentVariable(TEXT("MW_REFPOSE_DUMP_DIR"));
		if (Directory.IsEmpty())
		{
			return;
		}
		static int32 DumpIndex = 0;
		const FString WindowDirectory =
			Directory / FString::Printf(TEXT("w%02d"), DumpIndex++);
		IFileManager::Get().MakeDirectory(*WindowDirectory, true);

		const int32 BoneCount = Model.BoneCount;
		const int32 FrameCount = Model.FutureFrames;

		auto SaveFloats = [](TConstArrayView<float> Values, const FString& Path)
		{
			TArray<uint8> Bytes;
			Bytes.Append(
				reinterpret_cast<const uint8*>(Values.GetData()),
				Values.Num() * sizeof(float));
			FFileHelper::SaveArrayToFile(Bytes, *Path);
		};

		SaveFloats(Window.History, WindowDirectory / TEXT("history.f32"));
		SaveFloats(Window.RootPlan, WindowDirectory / TEXT("root_plan.f32"));
		SaveFloats(Window.Reference, WindowDirectory / TEXT("reference.f32"));
		SaveFloats(Window.Target, WindowDirectory / TEXT("target.f32"));
		SaveFloats(Window.Mask, WindowDirectory / TEXT("mask.f32"));
		SaveFloats(Joints, WindowDirectory / TEXT("joints.f32"));

		// 把模型输出的逐骨位置与作者参考位置按 [frame][bone][xyz] 落盘，
		// Python 侧据此独立判断「是模型输出在翻」还是「解算/重定向在翻」。
		TArray<float> PredictedPositions;
		PredictedPositions.Reserve(FrameCount * BoneCount * 3);
		TArray<float> ReferencePositions;
		ReferencePositions.Reserve(FrameCount * BoneCount * 3);
		for (int32 Frame = 0; Frame < FrameCount; ++Frame)
		{
			for (int32 Bone = 0; Bone < BoneCount; ++Bone)
			{
				const FVector3d& Position = Decoded[Frame].PositionsMeters[Bone];
				PredictedPositions.Add(static_cast<float>(Position.X));
				PredictedPositions.Add(static_cast<float>(Position.Y));
				PredictedPositions.Add(static_cast<float>(Position.Z));

				const FVector3d& Reference = Future[Frame].PositionsMeters[Bone];
				ReferencePositions.Add(static_cast<float>(Reference.X));
				ReferencePositions.Add(static_cast<float>(Reference.Y));
				ReferencePositions.Add(static_cast<float>(Reference.Z));
			}
		}
		SaveFloats(PredictedPositions, WindowDirectory / TEXT("predicted_positions.f32"));
		SaveFloats(ReferencePositions, WindowDirectory / TEXT("author_positions.f32"));

		// 求解后的局部变换：quat(xyzw) + translation，逐帧逐骨。
		TArray<float> LocalValues;
		LocalValues.Reserve(Local.Num() * BoneCount * 7);
		for (const TArray<FTransform>& Frame : Local)
		{
			for (const FTransform& Bone : Frame)
			{
				const FQuat Q = Bone.GetRotation();
				const FVector T = Bone.GetTranslation();
				LocalValues.Add(Q.X); LocalValues.Add(Q.Y); LocalValues.Add(Q.Z); LocalValues.Add(Q.W);
				LocalValues.Add(T.X); LocalValues.Add(T.Y); LocalValues.Add(T.Z);
			}
		}
		SaveFloats(LocalValues, WindowDirectory / TEXT("solved_local.f32"));

		TArray<FString> Meta;
		Meta.Add(FString::Printf(TEXT("bones=%d"), BoneCount));
		Meta.Add(FString::Printf(TEXT("frames=%d"), FrameCount));
		Meta.Add(FString::Printf(TEXT("history=%d"), Model.HistoryFrames));
		Meta.Add(FString::Printf(TEXT("fps=%.9f"), Model.FrameRate));
		FString Offsets;
		for (const int32 Offset : ReferenceOffsets)
		{
			Offsets += FString::Printf(TEXT("%s%d"), Offsets.IsEmpty() ? TEXT("") : TEXT(","), Offset);
		}
		Meta.Add(FString::Printf(TEXT("reference_offsets=%s"), *Offsets));
		Meta.Add(FString::Printf(TEXT("history_size=%d"), Window.History.Num()));
		Meta.Add(FString::Printf(TEXT("root_plan_size=%d"), Window.RootPlan.Num()));
		Meta.Add(FString::Printf(TEXT("reference_size=%d"), Window.Reference.Num()));
		Meta.Add(FString::Printf(TEXT("target_size=%d"), Window.Target.Num()));
		for (int32 Bone = 0; Bone < BoneCount; ++Bone)
		{
			Meta.Add(FString::Printf(
				TEXT("bone=%d name=%s parent=%d"),
				Bone, *Model.Bones[Bone].Name.ToString(), Model.Bones[Bone].Parent));
		}
		FFileHelper::SaveStringArrayToFile(Meta, *(WindowDirectory / TEXT("meta.txt")));
	}
}

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
	FString& OutError,
	TConstArrayView<bool> ChildUsable)
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

	// 模型原始输出 vs 作者参考：在做硬参考覆盖之前统计。
	// 这是「模型有没有跟上输入」的直接指标，也是坐标系/骨长/旋转先验
	// 装错时最先爆炸的量（实测装错旋转通道时 0.95 m，修好后 0.08 m）。
	{
		double SumCm = 0.0;
		int32 Count = 0;
		OutFrames.FramePredictionErrorCm.Reset(Decoded.Num());
		for (int32 FrameIndex = 0; FrameIndex < Decoded.Num(); ++FrameIndex)
		{
			if (!Future.IsValidIndex(FrameIndex))
			{
				OutFrames.FramePredictionErrorCm.Add(0.0);
				continue;
			}
			const TArray<FVector3d>& Predicted = Decoded[FrameIndex].PositionsMeters;
			const TArray<FVector3d>& Author = Future[FrameIndex].PositionsMeters;
			const int32 CompareCount = FMath::Min(Predicted.Num(), Author.Num());
			double FrameSumCm = 0.0;
			for (int32 BoneIndex = 0; BoneIndex < CompareCount; ++BoneIndex)
			{
				const double ErrorCm = FVector3d(Predicted[BoneIndex] - Author[BoneIndex]).Size() * 100.0;
				OutMetrics.MaxPredictionErrorCm =
					FMath::Max(OutMetrics.MaxPredictionErrorCm, ErrorCm);
				FrameSumCm += ErrorCm;
				SumCm += ErrorCm;
				++Count;
			}
			OutFrames.FramePredictionErrorCm.Add(
				CompareCount > 0 ? FrameSumCm / CompareCount : 0.0);
		}
		OutMetrics.MeanPredictionErrorCm = Count > 0 ? SumCm / Count : 0.0;
	}

	// 硬参考帧的位置必须用参考姿态本身，而不是模型输出。
	//
	// 训练损失里 `visible = 1 - mask`（model/MotionWeaver/refpose/losses.py），
	// 参考帧被明确排除在监督之外：模型在这些帧本来就没有被要求输出合理姿态。
	// 参考帧是模型的**已知输入**，不是猜测值；在这里用输入覆盖位置，
	// 既符合训练契约，也不会把「插值」当成模型对其它帧的预测。
	//
	// 旋转刻意不覆盖：作者快照是 UE 骨轴，模型旋转通道是 SOMA/BVH 骨轴，
	// 两者尚未同源（U1）。把快照旋转贴进相邻帧，会让单帧姿态相对前后帧
	// 翻 180°；旋转由求解器按位置重新推导，天然连续。
	for (const int32 Offset : ReferenceOffsets)
	{
		if (!Decoded.IsValidIndex(Offset) || !Future.IsValidIndex(Offset))
		{
			continue;
		}
		Decoded[Offset].PositionsMeters = Future[Offset].PositionsMeters;
	}

	TArray<TArray<FTransform>> Local;
	FRefPoseSkeletonSolver::FMetrics SolverMetrics;
	if (!FRefPoseSkeletonSolver::Solve(
		Parents, RefLocal, Decoded, Local, SolverMetrics, OutError, ChildUsable))
	{
		return false;
	}

	DumpWindowForDiagnosis(
		Model, Window, Joints, Decoded, Future, ReferenceOffsets, Local);

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
	OutFrames.Decoded = MoveTemp(Decoded);
	// 原始 6D 旋转单独保存：回灌要走「原样喂回」，不能解码再编码。
	OutFrames.RawRotations6D.SetNumUninitialized(
		Model.FutureFrames * BoneCount * FRefPosePoseContract::RotationStride);
	for (int32 Frame = 0; Frame < Model.FutureFrames; ++Frame)
	{
		for (int32 Bone = 0; Bone < BoneCount; ++Bone)
		{
			const float* Source = Joints.GetData()
				+ (static_cast<int64>(Frame) * BoneCount + Bone) * FRefPosePoseContract::BoneStride
				+ FRefPosePoseContract::PositionStride;
			FMemory::Memcpy(
				OutFrames.RawRotations6D.GetData()
					+ (Frame * BoneCount + Bone) * FRefPosePoseContract::RotationStride,
				Source,
				sizeof(float) * FRefPosePoseContract::RotationStride);
		}
	}
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
	FString& OutError,
	TConstArrayView<bool> ChildUsable)
{
	TSharedPtr<FRefPoseSession> Session = FRefPoseSession::Create(Model, OutError);
	if (!Session.IsValid())
	{
		return false;
	}
	return GenerateWindow(
		Model, *Session, History, HistoryRoot, Future, RootOrigin, FutureRoot,
		ReferenceOffsets, RefLocal, OutFrames, OutMetrics, OutError, ChildUsable);
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
		// UE 的 FTransform::operator* 是「先 A 后 B」：A*B 表示把 A 的结果
		// 再交给 B。父子链因此必须写成 Local * Parent（局部先、父级后），
		// 写成 Parent * Local 会把每根骨的平移按子骨旋转再转一次，
		// 整条骨架会散架（实测手臂能跑到骨盆下方 1.7 m）。
		SkeletonGlobal[Index] = Parent < 0
			? SkeletonReference[Index]
			: SkeletonReference[Index] * SkeletonGlobal[Parent];
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
	bool bEncodeReferenceRotations,
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
		// 同 BuildSolverReference：父子链是 Local * Parent，不是 Parent * Local。
		Global[Index] = Parent < 0
			? SkeletonLocal[Index]
			: SkeletonLocal[Index] * Global[Parent];
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

	// 临时取证：把 UE 侧组件空间骨位与骨架层级落盘一次，
	// 用于独立核对「链解算」与「模型坐标」哪一层出错。
	{
		const FString Directory =
			FPlatformMisc::GetEnvironmentVariable(TEXT("MW_REFPOSE_DUMP_DIR"));
		if (!Directory.IsEmpty())
		{
			static int32 DumpIndex = 0;
			const FString Prefix = Directory / FString::Printf(TEXT("ue_%02d"), DumpIndex++);
			TArray<float> Values;
			Values.Reserve(SkeletonCount * 3);
			for (int32 Index = 0; Index < SkeletonCount; ++Index)
			{
				const FVector& P = Global[Index].GetTranslation();
				Values.Add(P.X);
				Values.Add(P.Y);
				Values.Add(P.Z);
			}
			TArray<uint8> Bytes;
			Bytes.Append(
				reinterpret_cast<const uint8*>(Values.GetData()),
				Values.Num() * sizeof(float));
			FFileHelper::SaveArrayToFile(Bytes, *(Prefix + TEXT("_positions.f32")));
		}
	}

	// 姿态先去掉 UE 骨架自己的根变换：root motion 的唯一来源是姿态组轨迹，
	// 不能把快照里可能残留的根位移/朝向再叠一次。
	const FQuat4d RootRotationInv =
		FQuat4d(Global[SkeletonRoot].GetRotation()).GetNormalized().Inverse();
	const FVector3d RootLocation(Global[SkeletonRoot].GetTranslation());

	const FQuat4d TrackRotation = RootSample.Rotation.GetNormalized();
	const FQuat4d TrackRotationInv = TrackRotation.Inverse();

	// 模型帧的原点是「骨盆在地面上的水平投影」，不是 UE 骨架根骨。
	// 训练数据里 Hips 的 X/Z 恒为 0（std≈1e-4 m），高度全在 Y 上；
	// Root 骨则被钉在世界原点（p_root = −Rᵀ·k）。若这里按 UE 根骨取原点，
	// 再减一次姿态组 Root，骨盆会被推到 k 的负方向，归一化后放大到上千，
	// 模型输出随即变成畸形姿态。这里先取模型骨盆（Root 的第一个子骨）
	// 的组件空间位置，作为水平原点。
	int32 PelvisModelIndex = INDEX_NONE;
	for (int32 Index = 1; Index < ModelCount; ++Index)
	{
		if (Model.Bones[Index].Parent == 0)
		{
			PelvisModelIndex = Index;
			break;
		}
	}
	if (PelvisModelIndex == INDEX_NONE)
	{
		OutError = TEXT("关键帧姿态转换：模型根骨没有子骨，无法确定骨盆。");
		return false;
	}
	const int32 PelvisSkeletonIndex = Map.ModelToSkeleton[PelvisModelIndex];
	if (!Global.IsValidIndex(PelvisSkeletonIndex))
	{
		OutError = TEXT("关键帧姿态转换：骨盆没有映射到 UE 骨架。");
		return false;
	}
	const FVector PelvisComponent(
		RootRotationInv.RotateVector(
			Global[PelvisSkeletonIndex].GetTranslation() - RootLocation));
	const FVector3d PelvisMotion = FRefPosePoseContract::ToMotionPosition(PelvisComponent);
	// 只去掉水平分量：高度必须保留在姿态里（训练数据 k 的垂直分量恒为 0）。
	const FVector3d PelvisHorizontal(PelvisMotion.X, 0.0, PelvisMotion.Z);

	// 姿态组的垂直位移属于「角色站在哪」，不是根轨迹的水平分量；
	// 它留在身体姿态的高度里，水平位移则由 Root 轨迹负责。
	const FVector3d GroupLift(0.0, RootSample.PositionMeters.Y, 0.0);

	TArray<FVector3d> LocalPositions;
	TArray<FQuat4d> LocalRotations;
	TArray<bool> bResolved;
	LocalPositions.SetNumZeroed(ModelCount);
	LocalRotations.SetNum(ModelCount);
	bResolved.Init(false, ModelCount);

	// Root 是独立辅助轨道：世界位置固定在原点，局部位置 = -R(k)ᵀ·k_pos。
	// k 是骨盆的世界水平位置（不是姿态组原点），且垂直分量按合约取 0；
	// 否则归一化后的 Root.y 会变成几百（训练数据里它恒为 0）。
	const FVector3d WorldPelvisHorizontal =
		RootSample.PositionMeters + TrackRotation.RotateVector(PelvisHorizontal);
	const FVector3d RootTrackHorizontal(
		WorldPelvisHorizontal.X, 0.0, WorldPelvisHorizontal.Z);
	LocalPositions[0] = TrackRotationInv.RotateVector(-RootTrackHorizontal);
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

		// 身体骨相对骨盆的水平投影；Root 轨迹不再重复扣一次。
		LocalPositions[ModelIndex] = TrackRotationInv.RotateVector(
			MotionPosition + GroupLift - PelvisHorizontal);
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
		// 同样遵守 FTransform 的「先 A 后 B」：局部先叠加，再叠祖先偏移。
		AnchorToBone[ModelIndex] = LocalReference * AnchorToBone[Parent];

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

	// 模型契约的骨长才是模型看到的尺度。UE 人偶（SKM_UEFN_Mannequin）的
	// 小腿/前臂/手指与模型 SOMA 骨架最多差 40%，直接把 UE 位置喂进模型
	// 等于让它看一个从没见过的骨架：实测同一窗口位姿误差 0.9 m，重建出的
	// 骨长也不再刚性。这里按「UE 姿态的方向 + 模型契约的骨长」重建一遍，
	// 与求解器输出侧使用同一套「方向决定姿态、骨长取契约」的语义。
	TArray<FVector3d> RigidPositions = LocalPositions;
	for (int32 ModelIndex = 1; ModelIndex < ModelCount; ++ModelIndex)
	{
		// 骨盆挂在 Root 辅助轨道下，Root→Hips 不是刚性链：保留原位置。
		if (ModelIndex == PelvisModelIndex)
		{
			continue;
		}
		const int32 Parent = Model.Bones[ModelIndex].Parent;
		if (Parent < 0 || Parent >= ModelIndex)
		{
			continue;
		}
		const double Length = Model.Bones[ModelIndex].LocalPositionMeters.Size();
		if (Length <= UE_KINDA_SMALL_NUMBER)
		{
			continue;
		}
		const FVector3d Direction =
			(LocalPositions[ModelIndex] - LocalPositions[Parent]).GetSafeNormal();
		if (Direction.IsNearlyZero())
		{
			continue;
		}
		RigidPositions[ModelIndex] = RigidPositions[Parent] + Direction * Length;
	}

	OutPose.PositionsMeters = MoveTemp(RigidPositions);

	// 旋转通道：历史帧用「训练分布先验」，参考帧用「方向编码」。
	//
	// 为什么历史不能写成 UE 旋转：`r.npy` 的每根骨旋转是 SOMA/BVH 自己的局部
	// 骨轴约定，UE 的全局旋转与之不同源（U1 未闭合）。实测把 UE 旋转写进去，
	// 同一窗口的位姿误差 0.95 m、重建骨长最大偏差 0.51 m；改用 fixtures 的
	// 逐骨平均旋转后，误差降到 0.09 m、骨长偏差 0.12 m，播放起来才不是一团。
	//
	// 为什么参考帧反过来用方向编码：mask 命中的帧在模型里是 `where(mask,
	// reference, predicted)` 的硬覆盖，该帧的旋转会原样成为输出。若这里也写
	// 先验，参考帧就等于「只有位置、没有朝向」，模型拿不到目标姿态的方向。
	// 用「位置差分方向 + 每骨常量骨轴」编码虽不携带扭转，但把方向表达出来了，
	// 实测参考帧用方向编码时位姿误差 0.086 m，改用先验反而升到 0.29 m。
	const int32 RotationFloats = ModelCount * FRefPosePoseContract::RotationStride;
	const bool bHasPrior = Model.RotationPrior6D.Num() == RotationFloats;
	const bool bDirectionEncoded = bEncodeReferenceRotations
		&& Model.EncodeRotationsFromPositions(OutPose.PositionsMeters, OutPose.Rotations6D);

	if (!bDirectionEncoded)
	{
		if (bHasPrior)
		{
			OutPose.Rotations6D = Model.RotationPrior6D;
		}
		else
		{
			// 老包既没有先验也没有标定，只能退回 UE 旋转，并明确告警：
			// 这条路径的旋转与训练骨轴不同源（U1），姿态可能明显偏离。
			UE_LOG(LogTemp, Warning,
				TEXT("[RefPose] 模型包缺少旋转先验，退回 UE 旋转通道；")
				TEXT("旋转骨轴未与训练数据同源（U1），姿态可能偏离。"));
			OutPose.Rotations6D.SetNumUninitialized(RotationFloats);
			for (int32 ModelIndex = 0; ModelIndex < ModelCount; ++ModelIndex)
			{
				FRefPosePoseContract::Encode6D(
					LocalRotations[ModelIndex],
					OutPose.Rotations6D.GetData()
						+ ModelIndex * FRefPosePoseContract::RotationStride);
			}
		}
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

	{
		const FString Directory =
			FPlatformMisc::GetEnvironmentVariable(TEXT("MW_REFPOSE_DUMP_DIR"));
		if (!Directory.IsEmpty())
		{
			static int32 SnapshotDumpIndex = 0;
			if (SnapshotDumpIndex < 3)
			{
				const FString Prefix =
					Directory / FString::Printf(TEXT("snapshot_%02d"), SnapshotDumpIndex++);
				TArray<FString> Lines;
				TArray<float> Translations;
				Lines.Reserve(Snapshot.BoneNames.Num());
				for (int32 Slot = 0; Slot < Snapshot.BoneNames.Num(); ++Slot)
				{
					const FTransform& T = Snapshot.LocalTransforms[Slot];
					Translations.Add(T.GetTranslation().X);
					Translations.Add(T.GetTranslation().Y);
					Translations.Add(T.GetTranslation().Z);
					Lines.Add(FString::Printf(TEXT("%d %s loc=(%.4f,%.4f,%.4f) scale=(%.4f,%.4f,%.4f) rot=(%.4f,%.4f,%.4f,%.4f)"),
						Slot, *Snapshot.BoneNames[Slot].ToString(),
						T.GetTranslation().X, T.GetTranslation().Y, T.GetTranslation().Z,
						T.GetScale3D().X, T.GetScale3D().Y, T.GetScale3D().Z,
						T.GetRotation().X, T.GetRotation().Y, T.GetRotation().Z, T.GetRotation().W));
				}
				FFileHelper::SaveStringArrayToFile(Lines, *(Prefix + TEXT("_locals.txt")));
				TArray<uint8> Bytes;
				Bytes.Append(
					reinterpret_cast<const uint8*>(Translations.GetData()),
					Translations.Num() * sizeof(float));
				FFileHelper::SaveArrayToFile(Bytes, *(Prefix + TEXT("_locals.f32")));
			}
		}
	}

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

bool FRefPoseGenerator::TransferAuthoredTwist(
	TArray<TArray<FTransform>>& Generated,
	TConstArrayView<TArray<FTransform>> Author,
	TConstArrayView<int32> SkeletonParents,
	TConstArrayView<int32> SkeletonToModel,
	double& OutMeanTwistDeg,
	double& OutMaxTwistDeg,
	FString& OutError)
{
	OutMeanTwistDeg = 0.0;
	OutMaxTwistDeg = 0.0;

	if (Generated.Num() != Author.Num())
	{
		OutError = FString::Printf(
			TEXT("扭转迁移：生成帧数 %d 与作者帧数 %d 不一致。"),
			Generated.Num(), Author.Num());
		return false;
	}

	const int32 BoneCount = SkeletonParents.Num();
	if (BoneCount <= 0)
	{
		OutError = TEXT("扭转迁移：骨架没有骨骼。");
		return false;
	}

	int32 RootBone = INDEX_NONE;
	for (int32 BoneIndex = 0; BoneIndex < BoneCount; ++BoneIndex)
	{
		const int32 Parent = SkeletonParents[BoneIndex];
		if (Parent < -1 || Parent >= BoneIndex)
		{
			OutError = FString::Printf(
				TEXT("扭转迁移：骨 %d 的父索引 %d 非法（必须父先子后）。"),
				BoneIndex, Parent);
			return false;
		}
		if (Parent < 0 && RootBone == INDEX_NONE)
		{
			RootBone = BoneIndex;
		}
	}
	if (RootBone == INDEX_NONE)
	{
		OutError = TEXT("扭转迁移：骨架没有根骨。");
		return false;
	}

	// 对齐骨取骨盆（模型 Hips = 1）。作者快照与生成结果的整体朝向不同
	// （轨迹偏航 vs 快照朝向），只有先把作者姿态转到生成姿态的身体坐标系，
	// 逐骨的 roll 差才有意义；否则整段朝向差会被当成 roll 灌进每根骨。
	int32 AlignBone = RootBone;
	for (int32 BoneIndex = 0; BoneIndex < BoneCount; ++BoneIndex)
	{
		if (SkeletonToModel.IsValidIndex(BoneIndex)
			&& SkeletonToModel[BoneIndex] == 1)
		{
			AlignBone = BoneIndex;
			break;
		}
	}

	const FVector3d BoneAxis = FVector3d::ForwardVector;
	double SumAbsDeg = 0.0;
	int32 Count = 0;

	for (int32 FrameIndex = 0; FrameIndex < Generated.Num(); ++FrameIndex)
	{
		TArray<FTransform>& Frame = Generated[FrameIndex];
		const TArray<FTransform>& AuthorFrame = Author[FrameIndex];
		if (Frame.Num() != BoneCount || AuthorFrame.Num() != BoneCount)
		{
			OutError = FString::Printf(
				TEXT("扭转迁移：第 %d 帧骨数（生成 %d / 作者 %d / 父索引 %d）不一致。"),
				FrameIndex, Frame.Num(), AuthorFrame.Num(), BoneCount);
			return false;
		}

		// 迁移前的骨架空间位置是这一步的不变量：所有局部平移最后都从它
		// 反算，保证扭转只改 roll、不改任何关节位置。
		TArray<FVector3d> TargetPositions;
		TargetPositions.SetNumUninitialized(BoneCount);
		TArray<FQuat4d> GeneratedGlobal;
		TArray<FQuat4d> AuthorGlobal;
		GeneratedGlobal.SetNumUninitialized(BoneCount);
		AuthorGlobal.SetNumUninitialized(BoneCount);

		for (int32 BoneIndex = 0; BoneIndex < BoneCount; ++BoneIndex)
		{
			const int32 Parent = SkeletonParents[BoneIndex];
			const FQuat4d GeneratedLocal =
				FQuat4d(Frame[BoneIndex].GetRotation()).GetNormalized();
			const FQuat4d AuthorLocal =
				FQuat4d(AuthorFrame[BoneIndex].GetRotation()).GetNormalized();
			const FVector3d Translation(Frame[BoneIndex].GetTranslation());
			if (Parent < 0)
			{
				TargetPositions[BoneIndex] = Translation;
				GeneratedGlobal[BoneIndex] = GeneratedLocal;
				AuthorGlobal[BoneIndex] = AuthorLocal;
			}
			else
			{
				TargetPositions[BoneIndex] = TargetPositions[Parent]
					+ GeneratedGlobal[Parent].RotateVector(Translation);
				GeneratedGlobal[BoneIndex] =
					(GeneratedGlobal[Parent] * GeneratedLocal).GetNormalized();
				AuthorGlobal[BoneIndex] =
					(AuthorGlobal[Parent] * AuthorLocal).GetNormalized();
			}
		}

		// 用骨盆把作者姿态对齐到生成姿态，但**只取偏航**。
		//
		// 两侧的朝向差本质上是「角色面朝哪」（绕世界 Z 的偏航）；骨盆自身的
		// 俯仰/侧倾里混着模型误差，把整段旋转都拿来对齐等于把误差摊到每根骨，
		// 手腕、脚踝会被凭空拧几十度。只保留偏航既对齐了身体朝向，又不会
		// 把姿态误差当成 roll。
		FQuat4d Align = (GeneratedGlobal[AlignBone]
			* AuthorGlobal[AlignBone].Inverse()).GetNormalized();
		if (Align.W < 0.0)
		{
			Align = FQuat4d(-Align.X, -Align.Y, -Align.Z, -Align.W);
		}
		{
			const double YawLength = FMath::Sqrt(Align.Z * Align.Z + Align.W * Align.W);
			if (YawLength > UE_DOUBLE_SMALL_NUMBER)
			{
				Align = FQuat4d(0.0, 0.0, Align.Z / YawLength, Align.W / YawLength);
			}
			else
			{
				Align = FQuat4d::Identity;
			}
		}

		TArray<FQuat4d> NewGlobal;
		NewGlobal.SetNumUninitialized(BoneCount);

		// 父先子后：子骨必须基于父骨迁移后的世界旋转计算，否则父级的
		// roll 会被重复计算一次。
		for (int32 BoneIndex = 0; BoneIndex < BoneCount; ++BoneIndex)
		{
			const int32 Parent = SkeletonParents[BoneIndex];
			const FQuat4d GeneratedLocal =
				FQuat4d(Frame[BoneIndex].GetRotation()).GetNormalized();
			if (Parent < 0)
			{
				// Root 的世界朝向来自姿态组轨迹，不参与扭转迁移。
				NewGlobal[BoneIndex] = GeneratedGlobal[BoneIndex];
				continue;
			}
			const FQuat4d Current =
				(NewGlobal[Parent] * GeneratedLocal).GetNormalized();

			const int32 ModelIndex = SkeletonToModel.IsValidIndex(BoneIndex)
				? SkeletonToModel[BoneIndex] : INDEX_NONE;
			// 未映射骨保持 UE 参考旋转（手指/面部/扭转辅助骨都在这里），
			// 它们没有「模型骨轴」可比，强行迁移只会得到 180° 翻面。
			if (ModelIndex == INDEX_NONE
				|| BoneIndex == RootBone
				|| BoneIndex == AlignBone)
			{
				NewGlobal[BoneIndex] = Current;
				continue;
			}

			const FQuat4d Desired =
				(Align * AuthorGlobal[BoneIndex]).GetNormalized();
			// UE 骨骼约定局部 +X 就是骨轴。之前这里用
			// `2*atan2(Delta.X, Delta.W)` 从 `Current⁻¹·Desired` 里"抠"扭转：
			// 当模型与作者的摆动差接近 180° 时 Δ 是一个近乎垂直于 X 的
			// 半圈旋转，Δ.X 与 Δ.W 同时趋近 0，atan2 会给出 0 或 π，
			// 表现为「骨轴没动、roll 突然翻 180°」（实测 83 帧出现 >90° 跳变，
			// 而其中 27 帧在关闭扭转迁移后完全消失）。
			//
			// 改成「搬运作者整帧」：用最短弧把作者的骨轴转到生成结果的骨轴上，
			// 作者的 roll 随之平行搬运。骨轴严格等于生成结果（位置通道说了算），
			// roll 来自作者；只要两根骨轴不是正相反，这个映射连续且唯一。
			const FVector3d AuthorAxis = Desired.RotateVector(BoneAxis);
			const FVector3d CurrentAxis = Current.RotateVector(BoneAxis);
			const double AxisDot = AuthorAxis | CurrentAxis;
			if (!FMath::IsFinite(AxisDot) || AxisDot <= 0.5)
			{
				// 摆动差超过 60°：作者的 roll 相对这根骨轴已经没有意义，
				// 搬运过去的第二轴会绕着两根骨轴的差角乱转。保留模型自己的 roll。
				NewGlobal[BoneIndex] = Current;
				continue;
			}

			const FQuat4d Transport = FRefPosePoseContract::SwingBetween(
				AuthorAxis, CurrentAxis);
			const FQuat4d FullTransfer = (Transport * Desired).GetNormalized();

			// 权重必须连续：硬阈值会让同一根骨在「迁移 / 不迁移」之间反复切换，
			// 而两条路径的 roll 可以差到 180°，切换本身就是一次可见翻面
			// （实测模型误差 20–40 cm 时，大量手指骨正好在阈值附近来回跨）。
			// 这里用 AxisDot 做平滑权重：>=0.85（约 32°）完全迁移，
			// <=0.5（60°）完全不迁移，中间线性过渡。
			const double Weight = FMath::Clamp((AxisDot - 0.5) / (0.85 - 0.5), 0.0, 1.0);
			NewGlobal[BoneIndex] = Weight >= 1.0
				? FullTransfer
				: FQuat4d::Slerp(Current, FullTransfer, Weight).GetNormalized();

			// 统计量：实际搬运的扭转角（相对模型自身 roll 的差值），
			// 供诊断显示；它不是误差指标，只说明作者 roll 改了多少。
			const FQuat4d Delta = (Current.Inverse() * NewGlobal[BoneIndex]).GetNormalized();
			// 四元数双覆盖：W<0 时同一个旋转会被读成 360°-θ。先规范化，
			// 否则统计量会冒出 285°/360° 这种不可能的角度。
			const FQuat4d Canonical = Delta.W < 0.0
				? FQuat4d(-Delta.X, -Delta.Y, -Delta.Z, -Delta.W)
				: Delta;
			const double AbsDeg = FMath::RadiansToDegrees(
				FMath::Abs(2.0 * FMath::Atan2(Canonical.X, Canonical.W)));
			SumAbsDeg += AbsDeg;
			OutMaxTwistDeg = FMath::Max(OutMaxTwistDeg, AbsDeg);
			++Count;
		}

		// 分支骨（骨盆、胸、手）的子骨偏移不在骨轴上，绕轴旋转会把子骨带跑。
		// 用迁移前的骨架空间位置反算局部平移，把所有关节钉回原位。
		for (int32 BoneIndex = 0; BoneIndex < BoneCount; ++BoneIndex)
		{
			const int32 Parent = SkeletonParents[BoneIndex];
			const FVector Scale = Frame[BoneIndex].GetScale3D();
			if (Parent < 0)
			{
				Frame[BoneIndex] = FTransform(
					FQuat(NewGlobal[BoneIndex]),
					FVector(TargetPositions[BoneIndex]),
					Scale);
				continue;
			}
			const FQuat4d ParentInverse = NewGlobal[Parent].Inverse();
			Frame[BoneIndex] = FTransform(
				FQuat((ParentInverse * NewGlobal[BoneIndex]).GetNormalized()),
				FVector(ParentInverse.RotateVector(
					TargetPositions[BoneIndex] - TargetPositions[Parent])),
				Scale);
		}
	}

	OutMeanTwistDeg = Count > 0 ? SumAbsDeg / Count : 0.0;
	return true;
}
