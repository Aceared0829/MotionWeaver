// Copyright ZhaoZining. All Rights Reserved.

#include "MotionWeaverPoseActorFactory.h"

#include "MotionWeaverPoseActor.h"
#include "MotionWeaverPoseAsset.h"

#define LOCTEXT_NAMESPACE "MotionWeaverPoseActorFactory"

UMotionWeaverPoseActorFactory::UMotionWeaverPoseActorFactory(const FObjectInitializer& ObjectInitializer)
	: Super(ObjectInitializer)
{
	NewActorClass = AMotionWeaverPoseActor::StaticClass();
	DisplayName = LOCTEXT("PoseActorDisplayName", "MotionWeaver Pose Proxy");
	bUseSurfaceOrientation = false;
}

bool UMotionWeaverPoseActorFactory::CanCreateActorFrom(const FAssetData& AssetData, FText& OutErrorMsg)
{
	if (!AssetData.IsValid() || !AssetData.GetClass()->IsChildOf(UMotionWeaverPoseAsset::StaticClass()))
	{
		OutErrorMsg = LOCTEXT("NotAPoseAsset", "Only MotionWeaver Pose assets can be placed.");
		return false;
	}
	return true;
}

void UMotionWeaverPoseActorFactory::PostSpawnActor(UObject* NewAsset, AActor* NewActor)
{
	Super::PostSpawnActor(NewAsset, NewActor);

	if (AMotionWeaverPoseActor* PoseActor = Cast<AMotionWeaverPoseActor>(NewActor))
	{
		PoseActor->SetPoseAsset(Cast<UMotionWeaverPoseAsset>(NewAsset));
	}
}

#undef LOCTEXT_NAMESPACE
