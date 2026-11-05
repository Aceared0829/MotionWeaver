// Copyright ZhaoZining. All Rights Reserved.

#include "MotionWeaverPoseActor.h"

#include "MotionWeaverPoseAsset.h"
#include "MotionWeaverPoseComponent.h"
#include "Engine/SkeletalMesh.h"
#include "Materials/MaterialInterface.h"

AMotionWeaverPoseActor::AMotionWeaverPoseActor()
{
	PrimaryActorTick.bCanEverTick = false;
	PrimaryActorTick.bStartWithTickEnabled = false;

	PoseComponent = CreateDefaultSubobject<UMotionWeaverPoseComponent>(TEXT("PoseComponent"));
	RootComponent = PoseComponent;
}

void AMotionWeaverPoseActor::OnConstruction(const FTransform& Transform)
{
	Super::OnConstruction(Transform);
	SetPoseAsset(PoseAsset);
}

void AMotionWeaverPoseActor::PostLoad()
{
	Super::PostLoad();
	SetPoseAsset(PoseAsset);
}

void AMotionWeaverPoseActor::SetPoseAsset(UMotionWeaverPoseAsset* InPoseAsset)
{
	PoseAsset = InPoseAsset;

	if (PoseComponent == nullptr)
	{
		return;
	}

	PoseComponent->ApplyPoseAsset(PoseAsset);

	if (PoseAsset == nullptr)
	{
		return;
	}

	// 还原抽取时预览网格上的材质覆盖，顺序与来源一致。
	// 这里需要槽位索引，所以用带下标的遍历而不是直接取元素。
	for (int32 SlotIndex = 0; SlotIndex < PoseAsset->PreviewMaterials.Num(); ++SlotIndex)
	{
		UMaterialInterface* Material = PoseAsset->PreviewMaterials[SlotIndex].LoadSynchronous();
		if (Material != nullptr)
		{
			PoseComponent->SetMaterial(SlotIndex, Material);
		}
	}
}

#if WITH_EDITOR
void AMotionWeaverPoseActor::PostEditChangeProperty(FPropertyChangedEvent& PropertyChangedEvent)
{
	Super::PostEditChangeProperty(PropertyChangedEvent);

	if (PropertyChangedEvent.GetPropertyName() == GET_MEMBER_NAME_CHECKED(AMotionWeaverPoseActor, PoseAsset))
	{
		SetPoseAsset(PoseAsset);
	}
}
#endif
