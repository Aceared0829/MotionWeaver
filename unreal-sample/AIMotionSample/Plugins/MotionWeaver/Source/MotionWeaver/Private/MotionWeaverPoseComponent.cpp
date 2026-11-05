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

	if (BoneSpaceTransforms.IsEmpty())
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
	// 因此命中不满时明确告警，而不是悄悄跳过。
	if (MatchedCount < BoneSlotCount)
	{
		UE_LOG(LogMotionWeaver, Warning,
			TEXT("[%s] 姿势资产 %s 有 %d/%d 根骨骼在当前网格上找不到，这些骨骼保持参考姿态。"),
			*GetName(), *PoseAsset->GetName(), BoneSlotCount - MatchedCount, BoneSlotCount);
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
