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
#include "HAL/PlatformMisc.h"
#include "Misc/FileHelper.h"

namespace
{
	/**
	 * 接缝指标是否统计这根骨。
	 *
	 * UE5 骨架里带了大量非蒙皮辅助骨：`VB *`（武器/道具虚拟骨，离手 1 m 以上）、
	 * `ik_*`、`prop*`、`poi`、`attach`、`weapon_*`。它们的「位置跳变」只反映
	 * 父骨的 roll 转了多大的弧，和画面上的抽搐不是一回事；实测同一段结果里
	 * 含辅助骨是 159 cm，只算蒙皮骨是 74 cm。把它们显式排除，避免诊断数字
	 * 比实际观感夸张一个数量级。
	 */
	bool IsDeformingBoneForSeam(const FName& BoneName)
	{
		const FString Name = BoneName.ToString();
		static const TCHAR* const HelperPrefixes[] = {
			TEXT("VB "), TEXT("ik_"), TEXT("prop"), TEXT("poi"),
			TEXT("attach"), TEXT("weapon_"),
		};
		for (const TCHAR* Prefix : HelperPrefixes)
		{
			if (Name.StartsWith(Prefix, ESearchCase::IgnoreCase))
			{
				return false;
			}
		}
		return true;
	}

	/**
	 * 由局部变换累加出骨架空间位置与旋转。
	 *
	 * 与 FRefPoseSkeletonSolver::RebuildSkeletonPositions 同一套「父先子后」
	 * 语义，只是同时把旋转也带出来：蒙皮看到的是骨架空间旋转，roll 的变化
	 * 只能在全局旋转里量到。
	 */
	void AccumulateSkeletonSpace(
		TConstArrayView<int32> Parents,
		TConstArrayView<FTransform> Local,
		TArray<FVector>& OutPositions,
		TArray<FQuat4d>& OutRotations)
	{
		const int32 BoneCount = Local.Num();
		OutPositions.SetNumUninitialized(BoneCount);
		OutRotations.SetNumUninitialized(BoneCount);
		for (int32 BoneIndex = 0; BoneIndex < BoneCount; ++BoneIndex)
		{
			const int32 Parent = Parents.IsValidIndex(BoneIndex)
				? Parents[BoneIndex] : INDEX_NONE;
			const FQuat4d Rotation = FQuat4d(Local[BoneIndex].GetRotation()).GetNormalized();
			if (Parent < 0)
			{
				OutPositions[BoneIndex] = Local[BoneIndex].GetTranslation();
				OutRotations[BoneIndex] = Rotation;
			}
			else
			{
				OutPositions[BoneIndex] = OutPositions[Parent]
					+ OutRotations[Parent].RotateVector(Local[BoneIndex].GetTranslation());
				OutRotations[BoneIndex] = (OutRotations[Parent] * Rotation).GetNormalized();
			}
		}
	}

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
		bool bEncodeReferenceRotations,
		FRefPoseGenerator::FPoseInput& OutPose,
		FString& OutError,
		TArray<FTransform>* OutSkeletonLocal = nullptr)
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
			if (OutSkeletonLocal != nullptr)
			{
				*OutSkeletonLocal = LocalA;
			}
			return FRefPoseGenerator::SkeletonPoseToPoseInput(
				Model, Map, LocalA, SkeletonParents, RootSample,
				bEncodeReferenceRotations, OutPose, OutError);
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
		if (OutSkeletonLocal != nullptr)
		{
			*OutSkeletonLocal = Blended;
		}
		return FRefPoseGenerator::SkeletonPoseToPoseInput(
			Model, Map, Blended, SkeletonParents, RootSample,
			bEncodeReferenceRotations, OutPose, OutError);
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
	return Generate(Group, Model, Group.RefPoseGeneration, OutResult, OutError);
}

bool FRefPoseGroupGenerator::Generate(
	const UPoseGroupProxyAsset& Group,
	const URefPoseModelAsset& Model,
	const FRefPoseGenerationSettings& Settings,
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
	{
		const FString DumpDirectory =
			FPlatformMisc::GetEnvironmentVariable(TEXT("MW_REFPOSE_DUMP_DIR"));
		if (!DumpDirectory.IsEmpty())
		{
			TArray<FString> Lines;
			for (int32 Index = 0; Index < Reference.GetNum(); ++Index)
			{
				Lines.Add(FString::Printf(TEXT("bone=%d name=%s parent=%d"),
					Index, *SkeletonNames[Index].ToString(), SkeletonParents[Index]));
			}
			FFileHelper::SaveStringArrayToFile(Lines, *(DumpDirectory / TEXT("ue_bones.txt")));

			// 参考骨架的累计位置：用于核对「父先子后逐级相乘」这条链本身。
			TArray<FTransform> RefGlobal;
			RefGlobal.SetNumUninitialized(Reference.GetNum());
			TArray<float> RefPositions;
			RefPositions.Reserve(Reference.GetNum() * 3);
			for (int32 Index = 0; Index < Reference.GetNum(); ++Index)
			{
				const int32 Parent = Reference.GetParentIndex(Index);
				RefGlobal[Index] = Parent < 0
					? Reference.GetRefBonePose()[Index]
					: Reference.GetRefBonePose()[Index] * RefGlobal[Parent];
				const FVector& P = RefGlobal[Index].GetTranslation();
				RefPositions.Add(P.X);
				RefPositions.Add(P.Y);
				RefPositions.Add(P.Z);
			}
			TArray<uint8> RefBytes;
			RefBytes.Append(
				reinterpret_cast<const uint8*>(RefPositions.GetData()),
				RefPositions.Num() * sizeof(float));
			FFileHelper::SaveArrayToFile(RefBytes, *(DumpDirectory / TEXT("ue_ref_positions.f32")));
		}
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

	// 哪些模型骨可以当「朝向锚点」：目标 UE 骨架里存在的骨才算。
	// 模型多出来的 `*End` 与面部骨在目标骨架上不可见，模型对它们的预测
	// 不受绑定约束；让它们决定父骨朝向会让手指/头/脚掌整根翻 180°。
	TArray<bool> ChildUsable;
	ChildUsable.SetNumUninitialized(ModelCount);
	for (int32 ModelIndex = 0; ModelIndex < ModelCount; ++ModelIndex)
	{
		ChildUsable[ModelIndex] =
			Map.ModelToSkeleton.IsValidIndex(ModelIndex)
			&& Map.ModelToSkeleton[ModelIndex] != INDEX_NONE;
	}

	// 时间范围以「求解后的轨迹」为准，而不是原始关键帧跨度。
	// 超速段会被求解器做时长膨胀（例如 37 帧拉成 73 帧）；用原始跨度
	// 判断窗口是否存在，会把本来够长的轨迹误判成「短于一个完整窗口」。
	const int32 HistoryFrames = Model.HistoryFrames;
	const int32 FutureFrames = Model.FutureFrames;
	const int32 WindowSpan = HistoryFrames + FutureFrames;
	if (Samples.Num() < WindowSpan)
	{
		OutError = FString::Printf(
			TEXT("求解轨迹只有 %d 个样本，短于一个完整窗口 %d 帧。"),
			Samples.Num(), WindowSpan);
		return false;
	}

	// 起点：找到第一个「求解样本帧号 >= 起点关键帧帧号」的样本。
	// 模型需要它之前的 HistoryFrames 帧做 history，所以起点不能太靠前；
	// 不够时明确报错，不静默退回到第 0 帧——那会让作者以为参数生效了。
	if (!Group.Keys.IsValidIndex(Settings.StartKeyIndex))
	{
		OutError = FString::Printf(
			TEXT("起点关键帧下标 %d 越界（共 %d 个关键帧）。"),
			Settings.StartKeyIndex, Group.Keys.Num());
		return false;
	}
	const int32 StartKeyFrame = Group.Keys[Settings.StartKeyIndex].Frame;
	int32 StartSampleIndex = INDEX_NONE;
	for (int32 SampleIndex = 0; SampleIndex < Samples.Num(); ++SampleIndex)
	{
		if (Samples[SampleIndex].Frame >= StartKeyFrame)
		{
			StartSampleIndex = SampleIndex;
			break;
		}
	}
	if (StartSampleIndex == INDEX_NONE)
	{
		OutError = FString::Printf(
			TEXT("起点关键帧 %d（帧 %d）之后没有求解样本。"),
			Settings.StartKeyIndex, StartKeyFrame);
		return false;
	}
	// 索引 0 表示「动作起点」：模型仍然需要 24 帧前置历史，所以输出从
	// 第 HistoryFrames 个样本开始（与首版「整段滑窗」完全一致）。
	// 其它索引是作者显式指定的起点，必须自己备够历史——不够就报错，
	// 不静默退回到动作起点。
	if (Settings.StartKeyIndex == 0)
	{
		StartSampleIndex = FMath::Max(StartSampleIndex, HistoryFrames);
	}
	if (StartSampleIndex < HistoryFrames)
	{
		OutError = FString::Printf(
			TEXT("起点关键帧 %d（帧 %d，求解样本 %d）之前只有 %d 帧历史，")
			TEXT("少于模型需要的 %d 帧。请把起点后移，或在前置关键帧留下足够时间。"),
			Settings.StartKeyIndex, StartKeyFrame,
			Samples[StartSampleIndex].Frame, StartSampleIndex, HistoryFrames);
		return false;
	}

	// 多窗口连续生成：每个窗口吃 24 帧历史 + 24 帧未来，产出未来 24 帧。
	// 历史一律取作者参考姿态（关键帧插值），不把生成结果回灌。
	//
	// 回灌看起来能让相邻窗口「接着上一段继续」，但它会把上一窗口的误差
	// 当成事实喂回模型：实测同一动作第 1 个窗口位姿误差 0.08 m，第 2 个
	// 窗口涨到 0.29 m，到第 15 个窗口已漂到 0.72 m，而且误差会被下一窗口
	// 继续放大。作者参考姿态是这段动作里唯一独立的真值锚点；即使它与模型
	// 输出在接缝处不完全连续，也不会让整段越跑越歪。
	//
	// HistoryStart 是「第一个窗口历史的起点」；生成帧从 StartSampleIndex
	// 开始逐帧对应求解样本，因此第 i 个窗口的 Base = HistoryStart + i * 24。
	const int32 HistoryStart = StartSampleIndex - HistoryFrames;
	const int32 GeneratedCount = Samples.Num() - StartSampleIndex;
	const int32 WindowCount = (GeneratedCount + FutureFrames - 1) / FutureFrames;

	TArray<int32> Offsets;
	if (!Settings.ToReferenceOffsets(Offsets, OutError))
	{
		OutError = FString::Printf(TEXT("参考姿态档位无效：%s"), *OutError);
		return false;
	}

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
		MakeRootSample(Samples[StartSampleIndex - 1]);

	FRefPoseGenerator::FFrames AllModelFrames;
	AllModelFrames.Local.Reserve(GeneratedCount);
	TArray<FRefPoseWindowBuilder::FRootSample> PlaybackRoots;
	PlaybackRoots.Reserve(GeneratedCount);
	// 与每一帧生成结果一一对应的作者 UE 局部姿态。
	// 只用于迁移绕骨轴的自转（位置不可见的那一维），不参与位置/骨长。
	TArray<TArray<FTransform>> AuthorLocalPerFrame;
	AuthorLocalPerFrame.Reserve(GeneratedCount);
	FRefPoseGenerationMetrics TotalMetrics;
	int32 PredictionErrorFrameCount = 0;

	for (int32 WindowIndex = 0; WindowIndex < WindowCount; ++WindowIndex)
	{
		// Base 是「含历史」的绝对样本下标；ValidFuture 要按「已生成帧数」
		// 计算，不能用 Base 直接减——否则起点后移时会把整段历史算成本窗口
		// 已生成的帧，末窗口被错误截断。
		const int32 GeneratedOffset = WindowIndex * FutureFrames;
		const int32 Base = HistoryStart + GeneratedOffset;
		const int32 ValidFuture = FMath::Min(
			FutureFrames, GeneratedCount - GeneratedOffset);

		TArray<FRefPoseGenerator::FPoseInput> History;
		TArray<FRefPoseGenerator::FPoseInput> Future;
		TArray<FRefPoseWindowBuilder::FRootSample> HistoryRoot;
		TArray<FRefPoseWindowBuilder::FRootSample> FutureRoot;
		History.Reserve(HistoryFrames);
		Future.Reserve(FutureFrames);
		HistoryRoot.Reserve(HistoryFrames);
		FutureRoot.Reserve(FutureFrames);

		// 历史来源：作者参考姿态。首窗口与后续窗口走同一条路径，
		// 这样每个窗口的输入分布一致，也不会把上一窗口的误差累积下来。
		for (int32 Offset = 0; Offset < HistoryFrames; ++Offset)
		{
			const FChainTrajectorySample& Sample = Samples[Base + Offset];
			const FRefPoseWindowBuilder::FRootSample Root = MakeRootSample(Sample);
			FRefPoseGenerator::FPoseInput Input;
			const int32 KeyFrame = FMath::RoundToInt(
				MapSolvedFrameToKeyFrame(Group, SolvedDiagnostics, Sample.Frame));
			if (!SamplePoseAtFrame(
				Group, KeyFrame, Model, Map, SkeletonNames, ReferenceLocal,
				SkeletonParents, Root, /*bEncodeReferenceRotations=*/false,
				Input, OutError))
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
			TArray<FTransform> AuthorLocal;
			if (!SamplePoseAtFrame(
				Group, KeyFrame, Model, Map, SkeletonNames, ReferenceLocal,
				SkeletonParents, Root, /*bEncodeReferenceRotations=*/true,
				Input, OutError,
				Offset < ValidFuture ? &AuthorLocal : nullptr))
			{
				return false;
			}
			Future.Add(MoveTemp(Input));
			FutureRoot.Add(Root);
			if (Offset < ValidFuture)
			{
				PlaybackRoots.Add(Root);
				AuthorLocalPerFrame.Add(MoveTemp(AuthorLocal));
			}
		}

		FRefPoseGenerator::FFrames WindowFrames;
		FRefPoseGenerationMetrics WindowMetrics;
		if (!FRefPoseGenerator::GenerateWindow(
			Model, *Session, History, HistoryRoot, Future, HistoryRoot.Last(),
			FutureRoot, Offsets, ModelReference, WindowFrames, WindowMetrics, OutError,
			ChildUsable))
		{
			return false;
		}

		TotalMetrics.WindowCount += WindowMetrics.WindowCount;
		TotalMetrics.TotalInferenceMs += WindowMetrics.TotalInferenceMs;
		TotalMetrics.WindowInferenceMs.Add(WindowMetrics.TotalInferenceMs);
		TotalMetrics.MaxInferenceMs =
			FMath::Max(TotalMetrics.MaxInferenceMs, WindowMetrics.MaxInferenceMs);
		TotalMetrics.MaxBoneLengthDeviationCm = FMath::Max(
			TotalMetrics.MaxBoneLengthDeviationCm,
			WindowMetrics.MaxBoneLengthDeviationCm);
		// 最大值保留最差窗口，方便一眼看出哪一段跑偏。
		// 末窗口会用末帧补齐 24 帧输入，但补齐帧不是生成帧；平均值只累计
		// ValidFuture 帧，保证与 OutResult.FramePredictionErrorCm 是同一批帧。
		TotalMetrics.MaxPredictionErrorCm = FMath::Max(
			TotalMetrics.MaxPredictionErrorCm,
			WindowMetrics.MaxPredictionErrorCm);
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
			const double FramePredictionErrorCm =
				WindowFrames.FramePredictionErrorCm.IsValidIndex(Offset)
					? WindowFrames.FramePredictionErrorCm[Offset]
					: 0.0;
			OutResult.FramePredictionErrorCm.Add(FramePredictionErrorCm);
			TotalMetrics.MeanPredictionErrorCm += FramePredictionErrorCm;
			++PredictionErrorFrameCount;
			OutResult.FrameIsHardReference.Add(Offsets.Contains(Offset));
		}
	}

	// 重定向到 UE 骨架：只改有映射的骨，未映射的保持参考姿态。
	if (!Map.Retarget(
		AllModelFrames.Local, ModelParents, ReferenceLocal, SkeletonParents,
		OutResult.Frames, OutError))
	{
		return false;
	}

	if (Settings.bTransferAuthoredTwist)
	{
		if (AuthorLocalPerFrame.Num() != OutResult.Frames.Num())
		{
			OutError = FString::Printf(
				TEXT("扭转迁移：作者姿态帧数 %d 与生成帧数 %d 不一致。"),
				AuthorLocalPerFrame.Num(), OutResult.Frames.Num());
			return false;
		}
		if (!FRefPoseGenerator::TransferAuthoredTwist(
			OutResult.Frames, AuthorLocalPerFrame, SkeletonParents,
			Map.SkeletonToModel,
			TotalMetrics.MeanAuthoredTwistDeg, TotalMetrics.MaxAuthoredTwistDeg,
			OutError))
		{
			return false;
		}
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

	// Root 接缝体检：只看相邻帧的根平移差。
	// 它不能证明姿态正确，但窗口原点错位会立刻在这里放大。
	for (int32 FrameIndex = 1; FrameIndex < OutResult.Frames.Num(); ++FrameIndex)
	{
		if (OutResult.Frames[FrameIndex].Num() == 0
			|| OutResult.Frames[FrameIndex - 1].Num() == 0)
		{
			continue;
		}
		TotalMetrics.MaxRootSeamJumpCm = FMath::Max(
			TotalMetrics.MaxRootSeamJumpCm,
			static_cast<double>(
				(OutResult.Frames[FrameIndex][0].GetTranslation()
					- OutResult.Frames[FrameIndex - 1][0].GetTranslation()).Size()));
	}
	TotalMetrics.TotalTimeDilation = SolvedDiagnostics.TotalTimeDilation;
	TotalMetrics.GroupFrameRate = Group.Settings.FrameRate;
	TotalMetrics.ModelFrameRate = Model.FrameRate;
	TotalMetrics.StartSampleFrame = Samples[StartSampleIndex].Frame;
	TotalMetrics.PlaybackOriginFrame = Samples[StartSampleIndex - 1].Frame;
	TotalMetrics.ReferenceOffsets = Offsets;
	TotalMetrics.WindowStride = FutureFrames;

	// 逐帧调试信息：生成帧号、求解样本帧号、映射回作者关键帧的时间轴帧号。
	// 求解器会做时长膨胀，三者不是同一条时间轴；编辑器靠它们显示
	// 「当前帧在哪一段、对应哪个关键帧」，也靠它们做关键帧跳转。
	OutResult.FrameSolvedFrames.Reserve(GeneratedCount);
	OutResult.FrameAuthorFrames.Reserve(GeneratedCount);
	OutResult.FrameMaxRotationStepDeg.Reserve(GeneratedCount);
	OutResult.FrameWorstRotationBoneIndex.Reserve(GeneratedCount);
	OutResult.FrameMaxPoseStepCm.Reserve(GeneratedCount);
	OutResult.FrameWorstPoseBoneIndex.Reserve(GeneratedCount);

	// 接缝体检统一用「骨架空间」口径，并且只统计蒙皮骨：
	//
	//  - 位置跳变直接对应画面上的抽搐；
	//  - 旋转跳变必须看骨架空间，局部跳变会把「父骨转、子骨反向补偿」误报成抽搐；
	//  - 辅助骨（VB/ik/prop/poi/attach/weapon）离手足很远又不蒙皮，
	//    留在统计里会把最大值放大一个数量级（同一段结果实测含辅助骨
	//    159 cm，只算蒙皮骨 74 cm）。
	//
	// 排除哪些骨随结果带出，诊断里要写明口径，否则作者会以为指标被「调小了」。
	TArray<FVector> PreviousPositions;
	TArray<FQuat4d> PreviousRotations;
	TSet<FName> ExcludedBoneSet;

	for (int32 FrameIndex = 0; FrameIndex < GeneratedCount; ++FrameIndex)
	{
		const FChainTrajectorySample& Sample = Samples[StartSampleIndex + FrameIndex];
		OutResult.FrameSolvedFrames.Add(Sample.Frame);
		OutResult.FrameAuthorFrames.Add(
			MapSolvedFrameToKeyFrame(Group, SolvedDiagnostics, Sample.Frame));

		TArray<FVector> Positions;
		TArray<FQuat4d> Rotations;
		if (OutResult.Frames.IsValidIndex(FrameIndex))
		{
			AccumulateSkeletonSpace(
				SkeletonParents, OutResult.Frames[FrameIndex], Positions, Rotations);
		}

		double MaxRotationStepDeg = 0.0;
		int32 WorstRotationBoneIndex = INDEX_NONE;
		double MaxPoseStepCm = 0.0;
		int32 WorstPoseBoneIndex = INDEX_NONE;
		if (FrameIndex > 0
			&& Positions.Num() == PreviousPositions.Num()
			&& Positions.Num() > 0)
		{
			const int32 BoneCount = FMath::Min(Positions.Num(), SkeletonNames.Num());
			for (int32 BoneIndex = 1; BoneIndex < BoneCount; ++BoneIndex)
			{
				const FName BoneName = SkeletonNames[BoneIndex];
				if (!IsDeformingBoneForSeam(BoneName))
				{
					ExcludedBoneSet.Add(BoneName);
					continue;
				}

				const double PoseStepCm = static_cast<double>(
					(Positions[BoneIndex] - PreviousPositions[BoneIndex]).Size());
				if (PoseStepCm > MaxPoseStepCm)
				{
					MaxPoseStepCm = PoseStepCm;
					WorstPoseBoneIndex = BoneIndex;
				}

				const double StepDeg = FMath::RadiansToDegrees(
					Rotations[BoneIndex].AngularDistance(PreviousRotations[BoneIndex]));
				if (StepDeg > MaxRotationStepDeg)
				{
					MaxRotationStepDeg = StepDeg;
					WorstRotationBoneIndex = BoneIndex;
				}
			}
		}

		OutResult.FrameMaxRotationStepDeg.Add(MaxRotationStepDeg);
		OutResult.FrameWorstRotationBoneIndex.Add(WorstRotationBoneIndex);
		OutResult.FrameMaxPoseStepCm.Add(MaxPoseStepCm);
		OutResult.FrameWorstPoseBoneIndex.Add(WorstPoseBoneIndex);

		if (MaxRotationStepDeg > TotalMetrics.MaxGlobalRotationStepDeg)
		{
			TotalMetrics.MaxGlobalRotationStepDeg = MaxRotationStepDeg;
			TotalMetrics.MaxGlobalRotationStepBone = SkeletonNames.IsValidIndex(WorstRotationBoneIndex)
				? SkeletonNames[WorstRotationBoneIndex].ToString() : FString();
		}
		if (MaxPoseStepCm > TotalMetrics.MaxPoseStepCm)
		{
			TotalMetrics.MaxPoseStepCm = MaxPoseStepCm;
			TotalMetrics.MaxPoseStepBone = SkeletonNames.IsValidIndex(WorstPoseBoneIndex)
				? SkeletonNames[WorstPoseBoneIndex].ToString() : FString();
		}

		PreviousPositions = MoveTemp(Positions);
		PreviousRotations = MoveTemp(Rotations);
	}
	TotalMetrics.SeamMetricExcludedBones.Reset();
	for (const FName& BoneName : ExcludedBoneSet)
	{
		TotalMetrics.SeamMetricExcludedBones.Add(BoneName.ToString());
	}
	TotalMetrics.SeamMetricExcludedBones.Sort();

	// 作者参考姿态随结果带出：播放时作为「参考幽灵」与生成结果同帧对照。
	// 只用于对照显示，不参与播放帧的取值。
	OutResult.AuthorFrames = AuthorLocalPerFrame;
	for (int32 FrameIndex = 0; FrameIndex < OutResult.AuthorFrames.Num(); ++FrameIndex)
	{
		if (OutResult.Frames.IsValidIndex(FrameIndex)
			&& OutResult.Frames[FrameIndex].Num() > 0
			&& OutResult.AuthorFrames[FrameIndex].Num() > 0)
		{
			// 两者必须站在同一条求解轨迹上，否则对照时一个在原点、一个在几米外。
			OutResult.AuthorFrames[FrameIndex][0] =
				OutResult.Frames[FrameIndex][0];
		}
	}

	// 骨名随结果带出：编辑器不必为了显示「哪根骨跳变」再去加载一次网格。
	OutResult.SkeletonBoneNames = SkeletonNames;
	OutResult.SkeletonParents = SkeletonParents;

	// 锚点对齐：每个作者关键帧与「映射后时间最接近」的生成帧比较局部位姿。
	// 只统计已映射骨且跳过 Root：Root 平移来自求解轨迹，不来自姿态模型；
	// 未映射骨在生成结果里本来就是参考姿态，拿来比较没有信息量。
	// 结果标注为「锚点对齐误差」，不是模型精度——求解器改时长、硬参考覆盖
	// 都会计入，不能设成越小越好的门禁。
	if (OutResult.FrameAuthorFrames.Num() > 0
		&& OutResult.Frames.Num() == OutResult.FrameAuthorFrames.Num())
	{
		TArray<int32> MappedSkeletonBones;
		for (int32 BoneIndex = 1; BoneIndex < Map.SkeletonToModel.Num(); ++BoneIndex)
		{
			if (Map.SkeletonToModel[BoneIndex] != INDEX_NONE)
			{
				MappedSkeletonBones.Add(BoneIndex);
			}
		}

		for (int32 KeyIndex = 0; KeyIndex < Group.Keys.Num(); ++KeyIndex)
		{
			const FPoseKey& Key = Group.Keys[KeyIndex];
			if (Key.Frame < OutResult.FrameAuthorFrames[0] - 0.5
				|| Key.Frame > OutResult.FrameAuthorFrames.Last() + 0.5)
			{
				OutResult.AnchorSkips.Add(FString::Printf(
					TEXT("关键帧 %d（帧 %d）不在生成区间内，跳过锚点比较。"),
					KeyIndex, Key.Frame));
				continue;
			}
			if (Key.PoseAsset == nullptr || !Key.PoseAsset->IsValidPose())
			{
				OutResult.AnchorSkips.Add(FString::Printf(
					TEXT("关键帧 %d（帧 %d）没有有效姿态，跳过锚点比较。"),
					KeyIndex, Key.Frame));
				continue;
			}

			int32 NearestFrameIndex = 0;
			double NearestDistance = TNumericLimits<double>::Max();
			for (int32 FrameIndex = 0;
				FrameIndex < OutResult.FrameAuthorFrames.Num(); ++FrameIndex)
			{
				const double Distance =
					FMath::Abs(OutResult.FrameAuthorFrames[FrameIndex] - Key.Frame);
				if (Distance < NearestDistance)
				{
					NearestDistance = Distance;
					NearestFrameIndex = FrameIndex;
				}
			}

			TArray<FTransform> KeyLocal;
			if (!FRefPoseGenerator::SnapshotToSkeletonLocal(
				Key.PoseAsset->Snapshot, SkeletonNames, ReferenceLocal,
				KeyLocal, OutError))
			{
				return false;
			}

			// 取证：把作者关键帧的局部变换落盘，供离线脚本分解
			// 「骨轴误差 / 扭转误差」。只在显式设置导出目录时写。
			const FString DumpDirectory =
				FPlatformMisc::GetEnvironmentVariable(TEXT("MW_REFPOSE_DUMP_DIR"));
			if (!DumpDirectory.IsEmpty())
			{
				TArray<float> KeyValues;
				KeyValues.Reserve(KeyLocal.Num() * 7);
				for (const FTransform& Transform : KeyLocal)
				{
					const FVector& P = Transform.GetTranslation();
					const FQuat& Q = Transform.GetRotation();
					KeyValues.Add(P.X);
					KeyValues.Add(P.Y);
					KeyValues.Add(P.Z);
					KeyValues.Add(Q.X);
					KeyValues.Add(Q.Y);
					KeyValues.Add(Q.Z);
					KeyValues.Add(Q.W);
				}
				TArray<uint8> KeyBytes;
				KeyBytes.Append(
					reinterpret_cast<const uint8*>(KeyValues.GetData()),
					KeyValues.Num() * sizeof(float));
				FFileHelper::SaveArrayToFile(
					KeyBytes,
					*(DumpDirectory / FString::Printf(TEXT("key_%02d_locals.f32"), KeyIndex)));
			}

			FRefPoseAnchorAlignment Alignment;
			Alignment.KeyIndex = KeyIndex;
			Alignment.KeyFrame = Key.Frame;
			Alignment.GeneratedFrame = NearestFrameIndex;

			double PositionSum = 0.0;
			double RotationSum = 0.0;
			for (const int32 BoneIndex : MappedSkeletonBones)
			{
				if (!KeyLocal.IsValidIndex(BoneIndex)
					|| !OutResult.Frames[NearestFrameIndex].IsValidIndex(BoneIndex))
				{
					continue;
				}
				const FTransform& Generated =
					OutResult.Frames[NearestFrameIndex][BoneIndex];
				const FTransform& ReferenceTransform = KeyLocal[BoneIndex];
				const double PositionErrorCm = static_cast<double>(
					(Generated.GetTranslation()
						- ReferenceTransform.GetTranslation()).Size());
				const double RotationErrorDeg = FMath::RadiansToDegrees(
					Generated.GetRotation().AngularDistance(
						ReferenceTransform.GetRotation()));

				Alignment.MaxPositionErrorCm =
					FMath::Max(Alignment.MaxPositionErrorCm, PositionErrorCm);
				Alignment.MaxRotationErrorDeg =
					FMath::Max(Alignment.MaxRotationErrorDeg, RotationErrorDeg);
				PositionSum += PositionErrorCm;
				RotationSum += RotationErrorDeg;
				++Alignment.ComparedBoneCount;
			}
			if (Alignment.ComparedBoneCount > 0)
			{
				Alignment.MeanPositionErrorCm =
					PositionSum / Alignment.ComparedBoneCount;
				Alignment.MeanRotationErrorDeg =
					RotationSum / Alignment.ComparedBoneCount;
			}
			OutResult.AnchorAlignments.Add(Alignment);
		}
	}

	if (PredictionErrorFrameCount > 0)
	{
		TotalMetrics.MeanPredictionErrorCm /= PredictionErrorFrameCount;
	}
	OutResult.Metrics = TotalMetrics;

	OutResult.Metrics.FrameCount = OutResult.Frames.Num();
	OutResult.UnmappedBoneCount = Unmapped.Num();
	OutResult.SkeletonBoneCount = Reference.GetNum();
	OutResult.ModelBoneCount = ModelCount;
	OutError.Reset();
	return true;
}
