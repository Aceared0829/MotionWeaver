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

UMotionWeaverPoseAsset* AMotionWeaverPoseActor::GetPoseAsset() const
{
	// 以组件为准：组件才是真正解析姿态、驱动骨骼的那一方。
	return PoseComponent != nullptr ? PoseComponent->PoseAsset : PoseAsset;
}

void AMotionWeaverPoseActor::SetPoseAsset(UMotionWeaverPoseAsset* InPoseAsset)
{
	// 属性本身也存一份，是为了让细节面板的 Getter 在组件尚未创建时仍能显示。
	PoseAsset = InPoseAsset;

	if (PoseComponent == nullptr)
	{
		return;
	}

	PoseComponent->ApplyPoseAsset(InPoseAsset);
	PoseComponent->ApplyPreviewMaterials();
}

void AMotionWeaverPoseActor::OnConstruction(const FTransform& Transform)
{
	Super::OnConstruction(Transform);

	// 构造期把资产灌进组件；构造脚本可能刚改过 PoseAsset。
	SetPoseAsset(PoseAsset);
}

void AMotionWeaverPoseActor::PostLoad()
{
	Super::PostLoad();
	// 姿态应用留给组件的 OnRegister：那时渲染资源才就绪。
}
