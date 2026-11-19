// Copyright ZhaoZining. All Rights Reserved.

#include "RefPose/RefPoseGroupGenerator.h"

#include "Pose/Data/PoseGroupProxyAsset.h"
#include "Pose/Data/PoseProxyAsset.h"
#include "RefPose/RefPoseModel.h"
#include "RefPose/RefPosePoseContract.h"
#include "RefPose/RefPoseSession.h"
#include "RefPose/RefPoseSkeletonMap.h"

#include "Animation/Skeleton.h"
#include "Engine/SkeletalMesh.h"
#include "ReferenceSkeleton.h"

namespace
{
	int32 FindKeyIndex(const UPoseGroupProxyAsset& Asset, int32 Frame)
	{
		int32 Result = INDEX_NONE;
		for (int32 Index = 0; Index < Asset.Keys.Num(); ++Index)
		{
			if (Asset.Keys[Index].Frame <= Frame)
			{
				Result = Index;
			}
			else
			{
				break;
			}
		}
		return Result;
	}

	/**
	 * 把「求解后的帧号」映射回「作者关键帧时间」。
	 *
	 * 求解器对超速段做时长膨胀，样本帧号会被拉大（例如原始 200 帧变成 317 帧）。
	 * 参考姿态仍然来自作者的关键帧，如果直接拿膨胀后的帧号去查关键帧，
	 * 模型看到的历史/参考会与 Root 轨迹错相。这里按段内比例映射回去。
	 */
	double MapSolvedFrameToKeyFrame(
		const UPoseGroupProxyAsset& Asset,
		const FChainDiagnostics& Diagnostics,
		int32 SolvedFrame)
	{
		for (const FChainSegment& Segment : Diagnostics.Segments)
		{
			if (Segment.FrameCount <= 0)
			{
				continue;
			}
			const int32 EndFrame = Segment.StartFrame + Segment.FrameCount;
			if (SolvedFrame < Segment.StartFrame || SolvedFrame > EndFrame)
			{
				continue;
			}
			if (!Asset.Keys.IsValidIndex(Segment.KeyIndexA)
				|| !Asset.Keys.IsValidIndex(Segment.KeyIndexB))
			{
				break;
			}

			const double Alpha = static_cast<double>(SolvedFrame - Segment.StartFrame)
				/ static_cast<double>(Segment.FrameCount);
			return FMath::Lerp(
				static_cast<double>(Asset.Keys[Segment.KeyIndexA].Frame),
				static_cast<double>(Asset.Keys[Segment.KeyIndexB].Frame),
				Alpha);
		}
		return static_cast<double>(SolvedFrame);
	}

	/**
	 * 把姿态组的关键帧姿态转成模型输入。
	 *
	 * 快照存的是 UE 骨架（可能是 161 骨）的局部变换；模型要的是 78 骨、
	 * Root 局部、米制 Y-up。这里先按骨名解析到 UE 参考骨架槽位，
	 * 中间帧用相邻关键帧的局部姿态插值，再统一做骨架空间 → 模型 Root 局部的转换。
	 *
	 * 插值只用于提供模型 history/reference 的「已有姿态」，
	 * 最终播放帧仍由模型输出覆盖。
	 */
	bool SamplePoseAtFrame(
		const UPoseGroupProxyAsset& Asset,
		int32 Frame,
		const URefPoseModelAsset& Model,
		const FRefPoseSkeletonMap& Map,
		TConstArrayView<FName> SkeletonBoneNames,
		TConstArrayView<FTransform> SkeletonReference,
		TConstArrayView<int32> SkeletonParents,
		const FRefPoseWindowBuilder::FRootSample& RootSample,
		FRefPoseGenerator::FPoseInput& OutPose,
		FString& OutError)
	{
		const int32 KeyIndex = FindKeyIndex(Asset, Frame);
		if (KeyIndex == INDEX_NONE)
		{
			OutError = FString::Printf(TEXT("帧 %d 之前没有关键帧，无法取参考姿态。"), Frame);
			return false;
		}
		const FPoseKey& KeyA = Asset.Keys[KeyIndex];
		if (KeyA.PoseAsset == nullptr || !KeyA.PoseAsset->IsValidPose())
		{
			OutError = FString::Printf(TEXT("关键帧 %d 没有有效姿态。"), KeyA.Frame);
			return false;
		}

		TArray<FTransform> LocalA;
		if (!FRefPoseGenerator::SnapshotToSkeletonLocal(
			KeyA.PoseAsset->Snapshot, SkeletonBoneNames, SkeletonReference, LocalA, OutError))
		{
			return false;
		}

		// 落在关键帧上：直接用该帧姿态，不引入任何插值误差。
		if (KeyA.Frame == Frame || KeyIndex + 1 >= Asset.Keys.Num())
		{
			return FRefPoseGenerator::SkeletonPoseToPoseInput(
				Model, Map, LocalA, SkeletonParents, RootSample, OutPose, OutError);
		}

		const FPoseKey& KeyB = Asset.Keys[KeyIndex + 1];
		if (KeyB.PoseAsset == nullptr || !KeyB.PoseAsset->IsValidPose())
		{
			OutError = FString::Printf(TEXT("关键帧 %d 没有有效姿态。"), KeyB.Frame);
			return false;
		}

		const double Span = FMath::Max(1, KeyB.Frame - KeyA.Frame);
		const double Alpha = (Frame - KeyA.Frame) / Span;

		TArray<FTransform> LocalB;
		if (!FRefPoseGenerator::SnapshotToSkeletonLocal(
			KeyB.PoseAsset->Snapshot, SkeletonBoneNames, SkeletonReference, LocalB, OutError))
		{
			return false;
		}

		TArray<FTransform> Blended;
		if (!FRefPoseGenerator::BlendSkeletonLocal(
			LocalA, LocalB, Alpha, Blended, OutError))
		{
			return false;
		}
		return FRefPoseGenerator::SkeletonPoseToPoseInput(
			Model, Map, Blended, SkeletonParents, RootSample, OutPose, OutError);
	}

	const UPoseProxyAsset* FindPreviewPose(const UPoseGroupProxyAsset& Group)
	{
		for (const FPoseKey& Key : Group.Keys)
		{
			if (Key.PoseAsset != nullptr
				&& !Key.PoseAsset->PreviewMesh.IsNull()
				&& Key.PoseAsset->IsValidPose())
			{
				return Key.PoseAsset;
			}
		}
		return nullptr;
	}
}

bool FRefPoseGroupGenerator::Generate(
	const UPoseGroupProxyAsset& Group,
	const URefPoseModelAsset& Model,
	FRefPoseGroupGenerationResult& OutResult,
	FString& OutError)
{
	OutResult = FRefPoseGroupGenerationResult();

	if (!Model.HasUsableContract())
	{
		OutError = TEXT("模型缺少骨架契约或统计量。");
		return false;
	}
	if (Group.Keys.Num() < 2)
	{
		OutError = TEXT("姿态组至少需要两个关键帧。");
		return false;
	}
	if (!Group.IsWellFormed())
	{
		OutError = TEXT("姿态组结构无效：帧号必须严格递增，帧率必须大于 0。");
		return false;
	}

	const FChainDiagnostics& Diagnostics = Group.GetDiagnostics();
	const TArray<FChainTrajectorySample>& Samples = Group.GetSamples();
	if (Samples.Num() == 0)
	{
		OutError = Diagnostics.Errors.Num() > 0
			? FString::Printf(
				TEXT("姿态组没有可用的求解轨迹：%s"),
				*FString::Join(Diagnostics.Errors, TEXT("；")))
			: TEXT("姿态组没有可用的求解轨迹样本。");
		return false;
	}

	// 求解器的超速/转角门禁是创作质量判断，不是「轨迹不能用」：
	// 它仍会产出逐帧连续的样本。这里保留生成能力并把这些条目带出去，
	// 由编辑器显式提示，而不是让「生成并播放」整体失效。
	if (Diagnostics.Errors.Num() > 0)
	{
		OutResult.Warnings.Reserve(Diagnostics.Errors.Num());
		for (const FString& Diagnostic : Diagnostics.Errors)
		{
			OutResult.Warnings.Add(Diagnostic);
		}
	}
	if (Diagnostics.Warnings.Num() > 0)
	{
		OutResult.Warnings.Append(Diagnostics.Warnings);
	}
	if (!FMath::IsNearlyEqual(Group.Settings.FrameRate, Model.FrameRate, 0.01))
	{
		OutResult.Warnings.Add(FString::Printf(
			TEXT("姿态组帧率 %.3f 与模型契约帧率 %.3f 不一致，窗口时长会失真。"),
			Group.Settings.FrameRate, Model.FrameRate));
	}
	if (Diagnostics.TotalTimeDilation > 1.0 + 1e-3)
	{
		const double OriginalSeconds =
			(Group.Keys.Last().Frame - Group.Keys[0].Frame) / Group.Settings.FrameRate;
		const double SolvedSeconds = Samples.Num() / Group.Settings.FrameRate;
		OutResult.Warnings.Add(FString::Printf(
			TEXT("求解把时长拉长到 %.2fx：原始 %.2f s，求解后 %.2f s。")
			TEXT("若要严格按关键帧时长预览，把 Settings.MaxTimeDilation 设为 1.0。"),
			Diagnostics.TotalTimeDilation, OriginalSeconds, SolvedSeconds));
	}

	// 骨架来源：取第一个带预览网格的关键帧。
	const UPoseProxyAsset* PreviewPose = FindPreviewPose(Group);
	if (PreviewPose == nullptr)
	{
		OutError = TEXT("姿态组没有任何带预览网格的关键帧，无法确定骨架。");
		return false;
	}

	USkeletalMesh* Mesh = PreviewPose->PreviewMesh.LoadSynchronous();
	if (Mesh == nullptr || Mesh->GetSkeleton() == nullptr)
	{
		OutError = TEXT("预览网格或它引用的骨架加载失败。");
		return false;
	}
	const FReferenceSkeleton& Reference = Mesh->GetSkeleton()->GetReferenceSkeleton();
	if (Reference.GetNum() == 0)
	{
		OutError = TEXT("UE 参考骨架为空。");
		return false;
	}

	const int32 ModelCount = Model.Bones.Num();
	TArray<FName> ModelNames;
	TArray<int32> ModelParents;
	ModelNames.Reserve(ModelCount);
	ModelParents.Reserve(ModelCount);
	for (const FRefPoseBoneContract& Bone : Model.Bones)
	{
		ModelNames.Add(Bone.Name);
		ModelParents.Add(Bone.Parent);
	}

	TArray<FName> SkeletonNames;
	TArray<int32> SkeletonParents;
	SkeletonNames.Reserve(Reference.GetNum());
	SkeletonParents.Reserve(Reference.GetNum());
	for (int32 Index = 0; Index < Reference.GetNum(); ++Index)
	{
		SkeletonNames.Add(Reference.GetBoneName(Index));
		SkeletonParents.Add(Reference.GetParentIndex(Index));
	}

	FRefPoseSkeletonMap Map;
	TArray<FName> Unmapped;
	if (!FRefPoseSkeletonMap::Build(
		ModelNames, SkeletonNames, FRefPoseSkeletonMap::GetDefaultUe5Mapping(),
		Map, Unmapped, OutError))
	{
		return false;
	}

	// UE 参考姿态：重定向、求解器扭转、未映射中间骨补偿都以它为准。
	TArray<FTransform> ReferenceLocal;
	ReferenceLocal.Reserve(Reference.GetNum());
	for (int32 Index = 0; Index < Reference.GetNum(); ++Index)
	{
		ReferenceLocal.Add(Reference.GetRefBonePose()[Index]);
	}

	// 求解器参考：长度仍取模型契约，旋转取 UE 参考骨架的相对朝向。
	// 这样静止姿态能复现 UE 的肩/腕/踝扭转，而不是每根骨都从 +X 最短弧转。
	TArray<FTransform> ModelReference;
	if (!FRefPoseGenerator::BuildSolverReference(
		Model, Map, ReferenceLocal, SkeletonParents, ModelReference, OutError))
	{
		return false;
	}

	// 时间范围以「求解后的轨迹」为准，而不是原始关键帧跨度。
	// 超速段会被求解器做时长膨胀（例如 37 帧拉成 73 帧）；用原始跨度
	// 判断窗口是否存在，会把本来够长的轨迹误判成「短于一个完整窗口」。
	const int32 WindowSpan = Model.HistoryFrames + Model.FutureFrames;
	if (Samples.Num() < WindowSpan)
	{
		OutError = FString::Printf(
			TEXT("求解轨迹只有 %d 个样本，短于一个完整窗口 %d 帧。"),
			Samples.Num(), WindowSpan);
		return false;
	}

	// 多窗口连续生成：每个窗口吃 24 帧历史 + 24 帧未来，产出未来 24 帧。
	// 第一段历史就是模型看到的前置姿态；后续窗口用作者参考姿态作为历史，
	// 而不是把生成结果回灌——回灌会把误差累积进下一窗口，且与训练分布不一致。
	const int32 HistoryFrames = Model.HistoryFrames;
	const int32 FutureFrames = Model.FutureFrames;
	const int32 GeneratedCount = Samples.Num() - HistoryFrames;
	const int32 WindowCount = (GeneratedCount + FutureFrames - 1) / FutureFrames;

	FString SessionError;
	TSharedPtr<FRefPoseSession> Session = FRefPoseSession::Create(Model, SessionError);
	if (!Session.IsValid())
	{
		OutError = SessionError;
		return false;
	}

	const FChainDiagnostics& SolvedDiagnostics = Group.GetDiagnostics();
	auto MakeRootSample = [](const FChainTrajectorySample& Sample)
	{
		// Root 来自姿态组求解轨迹；这是本方案明确的能力边界：
		// 「模型预测 Root」不在本期范围。
		// LocalLocation 是 UE 厘米、相对 Actor；必须先做轴变换再进模型。
		FRefPoseWindowBuilder::FRootSample Root;
		Root.PositionMeters =
			FRefPosePoseContract::ToMotionPosition(Sample.LocalLocation);
		Root.Rotation = FRefPosePoseContract::ToMotionRotation(FQuat4d(
			FVector3d(0.0, 0.0, 1.0),
			FMath::DegreesToRadians(Sample.HeadingDegrees)));
		return Root;
	};

	// 播放的 Root 以「第一段历史末尾」为原点。所有窗口共用这个原点，
	// 接缝处才不会把每个窗口的 origin 重复叠加成跳变。
	const FRefPoseWindowBuilder::FRootSample PlaybackOrigin =
		MakeRootSample(Samples[HistoryFrames - 1]);

	FRefPoseGenerator::FFrames AllModelFrames;
	AllModelFrames.Local.Reserve(GeneratedCount);
	TArray<FRefPoseWindowBuilder::FRootSample> PlaybackRoots;
	PlaybackRoots.Reserve(GeneratedCount);
	FRefPoseGenerationMetrics TotalMetrics;
	const TArray<int32> Offsets = { 12, 23 };

	for (int32 WindowIndex = 0; WindowIndex < WindowCount; ++WindowIndex)
	{
		const int32 Base = WindowIndex * FutureFrames;
		const int32 ValidFuture = FMath::Min(FutureFrames, GeneratedCount - Base);

		TArray<FRefPoseGenerator::FPoseInput> History;
		TArray<FRefPoseGenerator::FPoseInput> Future;
		TArray<FRefPoseWindowBuilder::FRootSample> HistoryRoot;
		TArray<FRefPoseWindowBuilder::FRootSample> FutureRoot;
		History.Reserve(HistoryFrames);
		Future.Reserve(FutureFrames);
		HistoryRoot.Reserve(HistoryFrames);
		FutureRoot.Reserve(FutureFrames);

		for (int32 Offset = 0; Offset < HistoryFrames; ++Offset)
		{
			const FChainTrajectorySample& Sample = Samples[Base + Offset];
			const FRefPoseWindowBuilder::FRootSample Root = MakeRootSample(Sample);
			FRefPoseGenerator::FPoseInput Input;
			const int32 KeyFrame = FMath::RoundToInt(
				MapSolvedFrameToKeyFrame(Group, SolvedDiagnostics, Sample.Frame));
			if (!SamplePoseAtFrame(
				Group, KeyFrame, Model, Map, SkeletonNames, ReferenceLocal,
				SkeletonParents, Root, Input, OutError))
			{
				return false;
			}
			History.Add(MoveTemp(Input));
			HistoryRoot.Add(Root);
		}

		for (int32 Offset = 0; Offset < FutureFrames; ++Offset)
		{
			// 最后一个窗口可能不足 24 帧：用末帧补齐输入，输出再按 ValidFuture 截断。
			const int32 SampleIndex = FMath::Min(Base + HistoryFrames + Offset, Samples.Num() - 1);
			const FChainTrajectorySample& Sample = Samples[SampleIndex];
			const FRefPoseWindowBuilder::FRootSample Root = MakeRootSample(Sample);
			FRefPoseGenerator::FPoseInput Input;
			const int32 KeyFrame = FMath::RoundToInt(
				MapSolvedFrameToKeyFrame(Group, SolvedDiagnostics, Sample.Frame));
			if (!SamplePoseAtFrame(
				Group, KeyFrame, Model, Map, SkeletonNames, ReferenceLocal,
				SkeletonParents, Root, Input, OutError))
			{
				return false;
			}
			Future.Add(MoveTemp(Input));
			FutureRoot.Add(Root);
			if (Offset < ValidFuture)
			{
				PlaybackRoots.Add(Root);
			}
		}

		FRefPoseGenerator::FFrames WindowFrames;
		FRefPoseGenerationMetrics WindowMetrics;
		if (!FRefPoseGenerator::GenerateWindow(
			Model, *Session, History, HistoryRoot, Future, HistoryRoot.Last(),
			FutureRoot, Offsets, ModelReference, WindowFrames, WindowMetrics, OutError))
		{
			return false;
		}

		TotalMetrics.WindowCount += WindowMetrics.WindowCount;
		TotalMetrics.TotalInferenceMs += WindowMetrics.TotalInferenceMs;
		TotalMetrics.MaxInferenceMs =
			FMath::Max(TotalMetrics.MaxInferenceMs, WindowMetrics.MaxInferenceMs);
		TotalMetrics.MaxBoneLengthDeviationCm = FMath::Max(
			TotalMetrics.MaxBoneLengthDeviationCm,
			WindowMetrics.MaxBoneLengthDeviationCm);
		// 求解器指标是逐帧累计值，跨窗口应当求和而不是取最大。
		TotalMetrics.DegenerateBones += WindowMetrics.DegenerateBones;
		// 硬参考只统计真正进入播放输出的帧；末窗口补齐的 12/23 不算生成帧。
		for (const int32 Offset : Offsets)
		{
			if (Offset < ValidFuture)
			{
				++TotalMetrics.HardReferenceFrames;
			}
		}

		for (int32 Offset = 0; Offset < ValidFuture; ++Offset)
		{
			AllModelFrames.Local.Add(MoveTemp(WindowFrames.Local[Offset]));
		}
	}

	// 重定向到 UE 骨架：只改有映射的骨，未映射的保持参考姿态。
	if (!Map.Retarget(
		AllModelFrames.Local, ModelParents, ReferenceLocal, SkeletonParents,
		OutResult.Frames, OutError))
	{
		return false;
	}

	// 根位移/朝向：模型给的是 Root 局部空间的位置，需要写进组件变换才会真正走动。
	for (int32 FrameIndex = 0; FrameIndex < OutResult.Frames.Num(); ++FrameIndex)
	{
		if (!PlaybackRoots.IsValidIndex(FrameIndex) || OutResult.Frames[FrameIndex].Num() == 0)
		{
			continue;
		}
		// 用未来实际 Root 相对 origin 的位移，而不是模型吐出的 Root 槽位：
		// 模型输出的 Root 位置是辅助量，轨迹真相在 root_plan 里。
		const FRefPoseWindowBuilder::FRootSample& RootSample = PlaybackRoots[FrameIndex];
		// 位移必须表达在 origin 的朝向里，才是组件空间里的 Root 局部平移；
		// 直接拿世界位移会在起始朝向非零时把整条轨迹转歪。
		const FVector3d DeltaMeters = PlaybackOrigin.Rotation.Inverse().RotateVector(
			RootSample.PositionMeters - PlaybackOrigin.PositionMeters);
		OutResult.Frames[FrameIndex][0].SetTranslation(
			FRefPosePoseContract::ToUePosition(DeltaMeters));

		// 朝向同样来自 Root 轨迹：PoseChain 没有逐帧四元数，但按契约
		// Root 只有偏航，HeadingDegrees 足以构造相对 origin 的旋转。
		const FQuat4d DeltaRotation =
			PlaybackOrigin.Rotation.Inverse() * RootSample.Rotation;
		OutResult.Frames[FrameIndex][0].SetRotation(
			FQuat(FRefPosePoseContract::ToUeRotation(DeltaRotation)));
	}

	OutResult.Metrics = TotalMetrics;

	OutResult.Metrics.FrameCount = OutResult.Frames.Num();
	OutResult.UnmappedBoneCount = Unmapped.Num();
	OutResult.SkeletonBoneCount = Reference.GetNum();
	OutResult.ModelBoneCount = ModelCount;
	OutError.Reset();
	return true;
}
