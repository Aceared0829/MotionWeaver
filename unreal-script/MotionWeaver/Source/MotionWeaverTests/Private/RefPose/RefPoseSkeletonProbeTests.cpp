// Copyright ZhaoZining. All Rights Reserved.

#include "CoreMinimal.h"
#include "Animation/Skeleton.h"
#include "Engine/SkeletalMesh.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformMisc.h"
#include "Misc/FileHelper.h"
#include "Misc/AutomationTest.h"
#include "Pose/Data/PoseGroupProxyAsset.h"
#include "Pose/Data/PoseProxyAsset.h"
#include "RefPose/RefPoseGroupGenerator.h"
#include "RefPose/RefPoseModel.h"
#include "UObject/SoftObjectPath.h"
#include "UObject/StrongObjectPtr.h"

#if WITH_DEV_AUTOMATION_TESTS

/**
 * 骨架映射探查：模型契约是 78 根 SOMA 骨，UE 人偶是另一套骨名。
 * 能否直接蒙皮取决于骨名与骨序是否逐项一致；这里把实际值打出来取证。
 */
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FRefPoseSkeletonProbeTest,
	"MotionWeaver.RefPose.Probe.SkeletonBones",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRefPoseSkeletonProbeTest::RunTest(const FString& Parameters)
{
	const TCHAR* MeshPaths[] = {
		TEXT("/Game/Characters/UEFN_Mannequin/Meshes/SK_UEFN_Mannequin"),
		TEXT("/Game/Characters/UE5_Mannequins/Meshes/SKM_Manny"),
		TEXT("/Game/Characters/UE5_Mannequins/SK_Mannequin"),
	};

	for (const TCHAR* Path : MeshPaths)
	{
		USkeletalMesh* Mesh = Cast<USkeletalMesh>(FSoftObjectPath(Path).TryLoad());
		if (Mesh == nullptr)
		{
			AddInfo(FString::Printf(TEXT("MESH LOAD FAILED: %s"), Path));
			continue;
		}
		USkeleton* Skeleton = Mesh->GetSkeleton();
		if (Skeleton == nullptr)
		{
			AddInfo(FString::Printf(TEXT("NO SKELETON: %s"), Path));
			continue;
		}
		const FReferenceSkeleton& Reference = Skeleton->GetReferenceSkeleton();
		AddInfo(FString::Printf(TEXT("MESH %s skeleton=%s bones=%d"),
			Path, *Skeleton->GetName(), Reference.GetNum()));
		// 只打前 12 根：足够判断命名体系。
		for (int32 Index = 0; Index < FMath::Min(12, Reference.GetNum()); ++Index)
		{
			AddInfo(FString::Printf(TEXT("  BONE %d %s parent=%d"),
				Index, *Reference.GetBoneName(Index).ToString(),
				Reference.GetParentIndex(Index)));
		}
	}
	return true;
}

/**
 * 临时探针：把工程里实际使用的姿态组生成结果打成数据，定位「畸形」来源。
 * 不参与门禁，资产或模型包缺失时直接跳过。
 */
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FRefPoseUserGroupProbeTest,
	"MotionWeaver.RefPose.Probe.UserGroup",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRefPoseUserGroupProbeTest::RunTest(const FString& Parameters)
{
	const FString PackageDir =
		FPlatformMisc::GetEnvironmentVariable(TEXT("MW_REFPOSE_UE_PACKAGE"));
	if (PackageDir.IsEmpty())
	{
		AddInfo(TEXT("未设置 MW_REFPOSE_UE_PACKAGE，跳过。"));
		return true;
	}

	UPoseGroupProxyAsset* Group = Cast<UPoseGroupProxyAsset>(FSoftObjectPath(
		TEXT("/Game/Poses/NewMotionWeaverPoseGroupAsset.NewMotionWeaverPoseGroupAsset"))
		.TryLoad());
	if (Group == nullptr)
	{
		AddInfo(TEXT("姿态组资产加载失败，跳过。"));
		return true;
	}

	Group->Solve();
	AddInfo(FString::Printf(
		TEXT("GROUP keys=%d fps=%.3f samples=%d"),
		Group->Keys.Num(), Group->Settings.FrameRate, Group->GetSamples().Num()));
	for (int32 Index = 0; Index < Group->Keys.Num(); ++Index)
	{
		const FPoseKey& Key = Group->Keys[Index];
		FString MeshName = TEXT("<none>");
		if (Key.PoseAsset != nullptr && !Key.PoseAsset->PreviewMesh.IsNull())
		{
			if (USkeletalMesh* Mesh = Key.PoseAsset->PreviewMesh.LoadSynchronous())
			{
				MeshName = Mesh->GetName();
			}
		}
		AddInfo(FString::Printf(
			TEXT("  KEY %d frame=%d %.3fs pose=%s valid=%d mesh=%s loc=(%.1f,%.1f,%.1f) yaw=%.1f stop=%d"),
			Index, Key.Frame,
			Group->Settings.FrameRate > 0.0 ? Key.Frame / Group->Settings.FrameRate : 0.0,
			Key.PoseAsset != nullptr ? *Key.PoseAsset->GetName() : TEXT("<none>"),
			Key.PoseAsset != nullptr && Key.PoseAsset->IsValidPose() ? 1 : 0,
			*MeshName,
			Key.LocalLocation.X, Key.LocalLocation.Y, Key.LocalLocation.Z,
			Key.HeadingDegrees, Key.bStopHere ? 1 : 0));
	}
	for (const FString& Error : Group->GetDiagnostics().Errors)
	{
		AddInfo(FString::Printf(TEXT("  SOLVE ERROR: %s"), *Error));
	}

	FString Error;
	TStrongObjectPtr<URefPoseModelAsset> Model(
		URefPoseModelAsset::CreateFromOnnxFile(
			PackageDir / TEXT("model.onnx"), Error));
	if (!Model.IsValid() || !Model->LoadContractFromPackage(PackageDir, Error))
	{
		AddInfo(FString::Printf(TEXT("模型加载失败：%s"), *Error));
		return true;
	}

	FRefPoseGroupGenerationResult Result;
	if (!FRefPoseGroupGenerator::Generate(*Group, *Model, Result, Error))
	{
		AddInfo(FString::Printf(TEXT("生成失败：%s"), *Error));
		return true;
	}
	AddInfo(FString::Printf(
		TEXT("GEN frames=%d windows=%d hardref=%d maxrotstep=%.2f(%s) posestep=%.2f(%s) seam=%.2f dilation=%.3f unmapped=%d twistMean=%.2f twistMax=%.2f"),
		Result.Frames.Num(), Result.Metrics.WindowCount,
		Result.Metrics.HardReferenceFrames,
		Result.Metrics.MaxGlobalRotationStepDeg,
		*Result.Metrics.MaxGlobalRotationStepBone,
		Result.Metrics.MaxPoseStepCm,
		*Result.Metrics.MaxPoseStepBone,
		Result.Metrics.MaxRootSeamJumpCm,
		Result.Metrics.TotalTimeDilation, Result.UnmappedBoneCount,
		Result.Metrics.MeanAuthoredTwistDeg, Result.Metrics.MaxAuthoredTwistDeg));

	TArray<int32> SpikeFrames;
	for (int32 Index = 0; Index < Result.FrameMaxRotationStepDeg.Num(); ++Index)
	{
		SpikeFrames.Add(Index);
	}
	SpikeFrames.Sort([&Result](int32 A, int32 B)
	{
		return Result.FrameMaxRotationStepDeg[A] > Result.FrameMaxRotationStepDeg[B];
	});
	for (int32 Rank = 0; Rank < FMath::Min(15, SpikeFrames.Num()); ++Rank)
	{
		const int32 FrameIndex = SpikeFrames[Rank];
		const int32 BoneIndex = Result.FrameWorstRotationBoneIndex[FrameIndex];
		const FString BoneName = Result.SkeletonBoneNames.IsValidIndex(BoneIndex)
			? Result.SkeletonBoneNames[BoneIndex].ToString() : TEXT("?");
		AddInfo(FString::Printf(
			TEXT("  SPIKE frame=%d author=%.1f solved=%d step=%.2f bone=%s"),
			FrameIndex + 1,
			Result.FrameAuthorFrames[FrameIndex],
			Result.FrameSolvedFrames[FrameIndex],
			Result.FrameMaxRotationStepDeg[FrameIndex],
			*BoneName));
	}

	// 按骨统计整段最大跳变，找「哪根骨一直在翻」。
	// 直接复用逐帧的骨架空间指标：局部旋转会把「父骨转、子骨反向补偿」误算进来。
	TMap<int32, double> BoneMaxStep;
	for (int32 FrameIndex = 1; FrameIndex < Result.FrameMaxRotationStepDeg.Num(); ++FrameIndex)
	{
		const int32 BoneIndex = Result.FrameWorstRotationBoneIndex[FrameIndex];
		if (BoneIndex == INDEX_NONE)
		{
			continue;
		}
		double& Max = BoneMaxStep.FindOrAdd(BoneIndex);
		Max = FMath::Max(Max, Result.FrameMaxRotationStepDeg[FrameIndex]);
	}
	TArray<TPair<int32, double>> BoneSteps;
	for (const TPair<int32, double>& Pair : BoneMaxStep)
	{
		BoneSteps.Add(Pair);
	}
	BoneSteps.Sort([](const TPair<int32, double>& A, const TPair<int32, double>& B)
	{
		return A.Value > B.Value;
	});
	for (int32 Rank = 0; Rank < FMath::Min(15, BoneSteps.Num()); ++Rank)
	{
		const int32 BoneIndex = BoneSteps[Rank].Key;
		const FString BoneName = Result.SkeletonBoneNames.IsValidIndex(BoneIndex)
			? Result.SkeletonBoneNames[BoneIndex].ToString() : TEXT("?");
		AddInfo(FString::Printf(
			TEXT("  BONE %s maxstep=%.2f"),
			*BoneName, BoneSteps[Rank].Value));
	}

	for (const FRefPoseAnchorAlignment& Alignment : Result.AnchorAlignments)
	{
		AddInfo(FString::Printf(
			TEXT("  ANCHOR key=%d frame=%d gen=%d posMax=%.2fcm posMean=%.2fcm rotMax=%.2f rotMean=%.2f bones=%d"),
			Alignment.KeyIndex, Alignment.KeyFrame, Alignment.GeneratedFrame,
			Alignment.MaxPositionErrorCm, Alignment.MeanPositionErrorCm,
			Alignment.MaxRotationErrorDeg, Alignment.MeanRotationErrorDeg,
			Alignment.ComparedBoneCount));
	}

	// 对照：关闭「迁移关键帧扭转」再生成一次，核对两件事：
	//  1. 关节位置必须完全不变（扭转绕骨轴，位置是它的不变量）；
	//  2. 锚点旋转误差应当明显下降，否则说明扭转来源没取对。
	{
		FRefPoseGenerationSettings NoTwistSettings = Group->RefPoseGeneration;
		NoTwistSettings.bTransferAuthoredTwist = false;
		FRefPoseGroupGenerationResult NoTwist;
		FString NoTwistError;
		if (FRefPoseGroupGenerator::Generate(
			*Group, *Model, NoTwistSettings, NoTwist, NoTwistError))
		{
			double MaxJointDeltaCm = 0.0;
			if (NoTwist.Frames.Num() == Result.Frames.Num())
			{
				TArray<TArray<FVector>> WithPositions;
				TArray<TArray<FVector>> WithoutPositions;
				FRefPoseSkeletonSolver::RebuildSkeletonPositions(
					Result.SkeletonParents, Result.Frames, WithPositions, NoTwistError);
				FRefPoseSkeletonSolver::RebuildSkeletonPositions(
					Result.SkeletonParents, NoTwist.Frames, WithoutPositions, NoTwistError);
				for (int32 FrameIndex = 0;
					FrameIndex < WithPositions.Num() && FrameIndex < WithoutPositions.Num();
					++FrameIndex)
				{
					for (int32 BoneIndex = 0;
						BoneIndex < WithPositions[FrameIndex].Num()
							&& BoneIndex < WithoutPositions[FrameIndex].Num();
						++BoneIndex)
					{
						MaxJointDeltaCm = FMath::Max(
							MaxJointDeltaCm,
							static_cast<double>((WithPositions[FrameIndex][BoneIndex]
								- WithoutPositions[FrameIndex][BoneIndex]).Size()));
					}
				}
			}
			AddInfo(FString::Printf(
				TEXT("TWIST on: mean=%.2f max=%.2f | off: mean=%.2f max=%.2f | jointDeltaMax=%.3fcm"),
				Result.Metrics.MeanAuthoredTwistDeg, Result.Metrics.MaxAuthoredTwistDeg,
				NoTwist.Metrics.MeanAuthoredTwistDeg, NoTwist.Metrics.MaxAuthoredTwistDeg,
				MaxJointDeltaCm));

			// 对照导出：关闭扭转的逐帧局部变换。离线脚本据此验证
			// 「迁移只改 roll、关节位置逐字节不变」以及扭转残差是否真的收敛。
			const FString ProbeDumpDirectory =
				FPlatformMisc::GetEnvironmentVariable(TEXT("MW_REFPOSE_DUMP_DIR"));
			if (!ProbeDumpDirectory.IsEmpty() && NoTwist.Frames.Num() > 0)
			{
				const FString NoTwistDirectory = ProbeDumpDirectory / TEXT("notwist");
				IFileManager::Get().MakeDirectory(*NoTwistDirectory, true);
				const int32 ProbeBoneCount = NoTwist.SkeletonBoneNames.Num();
				TArray<float> LocalValues;
				LocalValues.Reserve(NoTwist.Frames.Num() * ProbeBoneCount * 7);
				for (const TArray<FTransform>& Frame : NoTwist.Frames)
				{
					for (int32 BoneIndex = 0; BoneIndex < ProbeBoneCount; ++BoneIndex)
					{
						const FTransform& Transform = Frame[BoneIndex];
						const FVector& Translation = Transform.GetTranslation();
						const FQuat& Rotation = Transform.GetRotation();
						LocalValues.Add(Translation.X);
						LocalValues.Add(Translation.Y);
						LocalValues.Add(Translation.Z);
						LocalValues.Add(Rotation.X);
						LocalValues.Add(Rotation.Y);
						LocalValues.Add(Rotation.Z);
						LocalValues.Add(Rotation.W);
					}
				}
				TArray<uint8> LocalBytes;
				LocalBytes.Append(
					reinterpret_cast<const uint8*>(LocalValues.GetData()),
					LocalValues.Num() * sizeof(float));
				FFileHelper::SaveArrayToFile(
					LocalBytes, *(NoTwistDirectory / TEXT("gen_locals.f32")));
			}

			for (int32 Index = 0; Index < Result.AnchorAlignments.Num(); ++Index)
			{
				const FRefPoseAnchorAlignment& On = Result.AnchorAlignments[Index];
				const FRefPoseAnchorAlignment& Off =
					NoTwist.AnchorAlignments.IsValidIndex(Index)
						? NoTwist.AnchorAlignments[Index] : On;
				AddInfo(FString::Printf(
					TEXT("  TWISTANCHOR key=%d rotOn mean=%.2f max=%.2f | rotOff mean=%.2f max=%.2f"),
					On.KeyIndex, On.MeanRotationErrorDeg, On.MaxRotationErrorDeg,
					Off.MeanRotationErrorDeg, Off.MaxRotationErrorDeg));
			}

			// 参考档位对照：确认「每 12 帧一次的姿态跳变」是否来自硬参考，
			// 而不是求解器或模型本身。只打印指标，不参与断言。
			const ERefPoseReferencePattern Patterns[] = {
				ERefPoseReferencePattern::None,
				ERefPoseReferencePattern::End,
				ERefPoseReferencePattern::Middle,
				ERefPoseReferencePattern::MiddleAndEnd,
			};
			for (const ERefPoseReferencePattern Pattern : Patterns)
			{
				FRefPoseGenerationSettings PatternSettings = Group->RefPoseGeneration;
				PatternSettings.ReferencePattern = Pattern;
				PatternSettings.bTransferAuthoredTwist = false;
				FRefPoseGroupGenerationResult PatternResult;
				FString PatternError;
				if (!FRefPoseGroupGenerator::Generate(
					*Group, *Model, PatternSettings, PatternResult, PatternError))
				{
					AddInfo(FString::Printf(
						TEXT("PATTERN %d 生成失败：%s"),
						static_cast<int32>(Pattern), *PatternError));
					continue;
				}
				AddInfo(FString::Printf(
					TEXT("PATTERN %d frames=%d hardref=%d maxrotstep=%.2f posestep=%.2f predMean=%.2f"),
					static_cast<int32>(Pattern), PatternResult.Frames.Num(),
					PatternResult.Metrics.HardReferenceFrames,
					PatternResult.Metrics.MaxGlobalRotationStepDeg,
					PatternResult.Metrics.MaxPoseStepCm,
					PatternResult.Metrics.MeanPredictionErrorCm));
			}
		}
		else
		{
			AddInfo(FString::Printf(TEXT("TWIST 对照生成失败：%s"), *NoTwistError));
		}
	}

	// 取证导出：生成结果、逐帧误差与帧映射。仅在显式设置环境变量时写盘，
	// 不参与断言；目的是离线用独立脚本核对「模型输出 / 重定向 / 播放」哪一层出错。
	const FString ProbeDumpDirectory =
		FPlatformMisc::GetEnvironmentVariable(TEXT("MW_REFPOSE_DUMP_DIR"));
	if (!ProbeDumpDirectory.IsEmpty() && Result.Frames.Num() > 0)
	{
		const int32 ProbeBoneCount = Result.SkeletonBoneNames.Num();
		TArray<float> LocalValues;
		LocalValues.Reserve(Result.Frames.Num() * ProbeBoneCount * 7);
		for (const TArray<FTransform>& Frame : Result.Frames)
		{
			for (int32 BoneIndex = 0; BoneIndex < ProbeBoneCount; ++BoneIndex)
			{
				const FTransform& Transform = Frame[BoneIndex];
				const FVector& Translation = Transform.GetTranslation();
				const FQuat& Rotation = Transform.GetRotation();
				LocalValues.Add(Translation.X);
				LocalValues.Add(Translation.Y);
				LocalValues.Add(Translation.Z);
				LocalValues.Add(Rotation.X);
				LocalValues.Add(Rotation.Y);
				LocalValues.Add(Rotation.Z);
				LocalValues.Add(Rotation.W);
			}
		}
		TArray<uint8> LocalBytes;
		LocalBytes.Append(
			reinterpret_cast<const uint8*>(LocalValues.GetData()),
			LocalValues.Num() * sizeof(float));
		FFileHelper::SaveArrayToFile(LocalBytes, *(ProbeDumpDirectory / TEXT("gen_locals.f32")));

		TArray<FString> FrameLines;
		FrameLines.Reserve(Result.Frames.Num());
		for (int32 FrameIndex = 0; FrameIndex < Result.Frames.Num(); ++FrameIndex)
		{
			FrameLines.Add(FString::Printf(
				TEXT("frame=%d author=%.3f solved=%d hardref=%d error=%.4f"),
				FrameIndex,
				Result.FrameAuthorFrames.IsValidIndex(FrameIndex)
					? Result.FrameAuthorFrames[FrameIndex] : -1.0,
				Result.FrameSolvedFrames.IsValidIndex(FrameIndex)
					? Result.FrameSolvedFrames[FrameIndex] : -1,
				Result.FrameIsHardReference.IsValidIndex(FrameIndex)
					&& Result.FrameIsHardReference[FrameIndex] ? 1 : 0,
				Result.FramePredictionErrorCm.IsValidIndex(FrameIndex)
					? Result.FramePredictionErrorCm[FrameIndex] : 0.0));
		}
		FFileHelper::SaveStringArrayToFile(
			FrameLines, *(ProbeDumpDirectory / TEXT("gen_frames.txt")));

		TArray<FString> BoneLines;
		BoneLines.Reserve(ProbeBoneCount);
		for (int32 BoneIndex = 0; BoneIndex < ProbeBoneCount; ++BoneIndex)
		{
			BoneLines.Add(FString::Printf(
				TEXT("%d %s"), BoneIndex,
				*Result.SkeletonBoneNames[BoneIndex].ToString()));
		}
		FFileHelper::SaveStringArrayToFile(
			BoneLines, *(ProbeDumpDirectory / TEXT("gen_bones.txt")));
	}
	return true;
}

#endif  // WITH_DEV_AUTOMATION_TESTS
