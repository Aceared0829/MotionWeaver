// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Assets/Factory/PoseActorFactory.h"

#include "Pose/Runtime/PoseActor.h"
#include "Pose/Data/PoseProxyAsset.h"

#define LOCTEXT_NAMESPACE "PoseActorFactory"

UPoseActorFactory::UPoseActorFactory(const FObjectInitializer& ObjectInitializer)
	: Super(ObjectInitializer)
{
	NewActorClass = APoseActor::StaticClass();
	DisplayName = LOCTEXT("PoseActorDisplayName", "MotionWeaver Pose Proxy");
	bUseSurfaceOrientation = false;
}

bool UPoseActorFactory::CanCreateActorFrom(const FAssetData& AssetData, FText& OutErrorMsg)
{
	if (!AssetData.IsValid() || !AssetData.GetClass()->IsChildOf(UPoseProxyAsset::StaticClass()))
	{
		OutErrorMsg = LOCTEXT("NotAPoseAsset", "Only MotionWeaver Pose assets can be placed.");
		return false;
	}
	return true;
}

void UPoseActorFactory::PostSpawnActor(UObject* NewAsset, AActor* NewActor)
{
	Super::PostSpawnActor(NewAsset, NewActor);

	if (APoseActor* PoseActor = Cast<APoseActor>(NewActor))
	{
		PoseActor->SetPoseAsset(Cast<UPoseProxyAsset>(NewAsset));
	}
}

#undef LOCTEXT_NAMESPACE
