// Copyright ZhaoZining. All Rights Reserved.

#include "CoreMinimal.h"
#include "Animation/Skeleton.h"
#include "Engine/SkeletalMesh.h"
#include "Misc/AutomationTest.h"
#include "UObject/SoftObjectPath.h"

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

#endif  // WITH_DEV_AUTOMATION_TESTS
