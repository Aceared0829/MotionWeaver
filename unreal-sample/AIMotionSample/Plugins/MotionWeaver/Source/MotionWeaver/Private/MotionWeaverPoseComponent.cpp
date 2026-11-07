// Copyright ZhaoZining. All Rights Reserved.

#include "MotionWeaverPoseComponent.h"

#include "MotionWeaverLog.h"
#include "MotionWeaverPoseAsset.h"
#include "Engine/SkeletalMesh.h"
#include "Materials/MaterialInterface.h"
#include "ReferenceSkeleton.h"

UMotionWeaverPoseComponent::UMotionWeaverPoseComponent(const FObjectInitializer& ObjectInitializer)
	: Super(ObjectInitializer)
{
	// 显示一份静态姿态不需要每帧求值。
	PrimaryComponentTick.bCanEverTick = false;
	PrimaryComponentTick.bStartWithTickEnabled = false;
	bEnableUpdateRateOptimizations = false;
}

void UMotionWeaverPoseComponent::OnRegister()
{
	Super::OnRegister();
	ApplyPoseAsset(PoseAsset);
}

void UMotionWeaverPoseComponent::PostLoad()
{
	Super::PostLoad();

	// PostLoad 阶段渲染资源还没就绪（SetSkinnedAssetAndUpdate 会碰 PrecachePSOs/LOD），
	// 这里只把资产指针接好；真正的应用留到 OnRegister，避免同一件事做两次。
	// PoseAsset 已是反序列化后的值，不需要额外处理。
}

void UMotionWeaverPoseComponent::ApplyPoseAsset(UMotionWeaverPoseAsset* InPoseAsset)
{
	PoseAsset = InPoseAsset;

	if (PoseAsset == nullptr)
	{
		return;
	}

	// 资产记录的预览网格优先，保证"拖进关卡就长得和抽取时一样"。
	if (USkeletalMesh* Mesh = PoseAsset->PreviewMesh.LoadSynchronous())
	{
		// 注意：SetSkinnedAssetAndUpdate 在 mesh 与当前相同时会直接 return，
		// 不会重跑 AllocateTransformData，所以不能指望它来清理上一次的姿态残留。
		SetSkinnedAssetAndUpdate(Mesh);
	}

	ApplySnapshotToBones();

	// 材质必须在这里一并应用，不能只挂在调用方的构造路径上。
	// 打包构建（cooked）下 World 不重跑构造脚本（World.cpp:2764 的
	// bRerunConstructionScripts = !RequiresCookedData()），从关卡反序列化出来的
	// Actor 只走 OnRegister——那时若只灌骨骼不灌材质，外观就会退回网格默认材质。
	ApplyPreviewMaterials();
}

void UMotionWeaverPoseComponent::SetPoseAssetDeferred(UMotionWeaverPoseAsset* InPoseAsset)
{
	// 仅登记，不触碰渲染状态；应用留给 OnConstruction / OnRegister。
	PoseAsset = InPoseAsset;
}

void UMotionWeaverPoseComponent::RefreshFromAsset()
{
	ApplyPoseAsset(PoseAsset);
}

void UMotionWeaverPoseComponent::ApplyPreviewMaterials()
{
	if (PoseAsset == nullptr)
	{
		return;
	}

	// 按资产记录的槽位顺序还原，与抽取时的画面保持一致。
	// 关卡实例和编辑器视口都走这里，避免两处实现将来分叉。
	const TArray<TSoftObjectPtr<UMaterialInterface>>& Materials = PoseAsset->PreviewMaterials;
	for (int32 SlotIndex = 0; SlotIndex < Materials.Num(); ++SlotIndex)
	{
		UMaterialInterface* Material = Materials[SlotIndex].LoadSynchronous();
		if (Material != nullptr)
		{
			SetMaterial(SlotIndex, Material);
		}
	}
}

void UMotionWeaverPoseComponent::ApplySnapshotToBones()
{
	if (PoseAsset == nullptr || !PoseAsset->IsValidPose())
	{
		return;
	}

	// 名字不能叫 SkinnedAsset：USkinnedMeshComponent 有同名成员，会触发 C4458 声明遮蔽。
	const USkinnedAsset* CurrentSkinnedAsset = GetSkinnedAsset();
	if (CurrentSkinnedAsset == nullptr)
	{
		// 没有网格就没有参考骨架，这时写任何骨骼都没有依据。
		return;
	}

	// 骨骼数组在 AllocateTransformData 里分配，而后者只在组件已注册时执行
	// （SkinnedMeshComponent.cpp:2793 的 IsRegistered() || IsPreRegistering()）。
	// 构造脚本阶段组件尚未注册，所以这里可能是空的——真正生效靠随后的 OnRegister。
	// 一旦将来有人在 OnRegister 之前换网格，这条链路会静默失效，因此留一条可查的痕迹。
	if (BoneSpaceTransforms.Num() != CurrentSkinnedAsset->GetRefSkeleton().GetNum())
	{
		UE_LOG(LogMotionWeaver, Verbose,
			TEXT("[%s] 骨骼数组尚未按当前网格分配（%d vs %d），本次不写入姿态，等待 OnRegister。"),
			*GetName(), BoneSpaceTransforms.Num(), CurrentSkinnedAsset->GetRefSkeleton().GetNum());
		return;
	}

	const FMotionWeaverPoseSnapshot& Snapshot = PoseAsset->Snapshot;
	const int32 BoneSlotCount = FMath::Min(Snapshot.BoneNames.Num(), Snapshot.LocalTransforms.Num());

	if (BoneSlotCount == 0)
	{
		return;
	}

	// 先整体回到参考姿态。
	// 这一步不能省：同一网格连续应用两个不同资产时，上一个资产写过的骨骼不会自动复位，
	// 缺失的骨骼会保留上一次的值，画面看起来"正常"但数据是错的。
	BoneSpaceTransforms = CurrentSkinnedAsset->GetRefSkeleton().GetRefBonePose();

	// 骨名与变换是等长并行数组，按位配对；需要下标同时取两处，范围 for 反而更绕。
	int32 MatchedCount = 0;
	for (int32 BoneSlot = 0; BoneSlot < BoneSlotCount; ++BoneSlot)
	{
		const FName BoneName = Snapshot.BoneNames[BoneSlot];
		if (BoneName == NAME_None)
		{
			continue;
		}

		const int32 BoneIndex = GetBoneIndex(BoneName);
		if (BoneSpaceTransforms.IsValidIndex(BoneIndex))
		{
			BoneSpaceTransforms[BoneIndex] = Snapshot.LocalTransforms[BoneSlot];
			++MatchedCount;
		}
	}

	// 静默产出半套姿态是最坏的结果：调用方无法察觉，下游会把错误数据当成真值。
	// 因此命中不满时明确告警——但只在结果发生变化时打一次：
	// OnRegister 与构造脚本重跑都会走到这里，无条件打印会刷屏。
	struct FLastMismatchReport
	{
		const UObject* Component = nullptr;
		const UObject* Asset = nullptr;
		int32 MissingCount = 0;
	};
	static FLastMismatchReport LastMismatch;

	if (MatchedCount < BoneSlotCount)
	{
		const int32 MissingCount = BoneSlotCount - MatchedCount;
		const bool bAlreadyReported =
			LastMismatch.Component == this &&
			LastMismatch.Asset == PoseAsset &&
			LastMismatch.MissingCount == MissingCount;

		if (!bAlreadyReported)
		{
			UE_LOG(LogMotionWeaver, Warning,
				TEXT("[%s] 姿势资产 %s 有 %d/%d 根骨骼在当前网格上找不到，这些骨骼保持参考姿态。"),
				*GetName(), *PoseAsset->GetName(), MissingCount, BoneSlotCount);

			LastMismatch = { this, PoseAsset, MissingCount };
		}
	}
	else
	{
		LastMismatch = FLastMismatchReport();
	}

	// 通知组件骨骼已改动，重建组件空间矩阵。
	MarkRefreshTransformDirty();
	RefreshBoneTransforms();
}

#if WITH_EDITOR
void UMotionWeaverPoseComponent::PostEditChangeProperty(FPropertyChangedEvent& PropertyChangedEvent)
{
	Super::PostEditChangeProperty(PropertyChangedEvent);

	if (PropertyChangedEvent.GetPropertyName() == GET_MEMBER_NAME_CHECKED(UMotionWeaverPoseComponent, PoseAsset))
	{
		ApplyPoseAsset(PoseAsset);
	}
}
#endif
