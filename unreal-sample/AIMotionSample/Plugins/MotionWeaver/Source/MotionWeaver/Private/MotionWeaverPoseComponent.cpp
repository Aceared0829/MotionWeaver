// Copyright ZhaoZining. All Rights Reserved.

#include "MotionWeaverPoseComponent.h"

#include "MotionWeaverPoseAsset.h"
#include "Engine/SkeletalMesh.h"

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
	ApplyPoseAsset(PoseAsset);
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
		SetSkinnedAssetAndUpdate(Mesh, /*bReinitPose*/ true);
	}

	ApplySnapshotToBones();
}

void UMotionWeaverPoseComponent::RefreshFromAsset()
{
	ApplyPoseAsset(PoseAsset);
}

void UMotionWeaverPoseComponent::ApplySnapshotToBones()
{
	if (PoseAsset == nullptr || !PoseAsset->IsValidPose())
	{
		return;
	}

	const FMotionWeaverPoseSnapshot& Snapshot = PoseAsset->Snapshot;

	// PoseableMeshComponent 的 BoneSpaceTransforms 本身就是逐骨局部空间（相对父骨），
	// 与快照语义一致，直接写这个数组，不走 SetBoneTransformByName 的空间换算。
	if (BoneSpaceTransforms.IsEmpty())
	{
		return;
	}

	const int32 BoneSlotCount = FMath::Min(Snapshot.BoneNames.Num(), Snapshot.LocalTransforms.Num());

	// 骨名与变换是等长并行数组，按位配对；这里需要下标同时取两处，范围 for 反而更绕。
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
		}
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
