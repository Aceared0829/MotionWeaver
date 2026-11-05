// Copyright ZhaoZining. All Rights Reserved.

#include "MotionWeaverPoseActor.h"

#include "MotionWeaverPoseAsset.h"
#include "MotionWeaverPoseComponent.h"

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

	// 组件是唯一真值持有者；Actor 侧这个字段只是镜像，方便在 Actor 上直接指定。
	PoseComponent->ApplyPoseAsset(PoseAsset);
	PoseComponent->ApplyPreviewMaterials();
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
