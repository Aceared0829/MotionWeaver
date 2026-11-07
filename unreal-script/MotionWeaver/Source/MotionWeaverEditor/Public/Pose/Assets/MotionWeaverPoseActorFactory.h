// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "ActorFactories/ActorFactory.h"
#include "MotionWeaverPoseActorFactory.generated.h"

/**
 * 把姿势代理资产拖进关卡时创建 AMotionWeaverPoseActor。
 *
 * 与静态网格体工厂同构：资产是数据、Actor 是载体。
 * bShouldAutoRegister 保持默认 true，编辑器会在加载时自动登记这个工厂。
 */
UCLASS(MinimalAPI)
class UMotionWeaverPoseActorFactory final : public UActorFactory
{
	GENERATED_BODY()

public:
	UMotionWeaverPoseActorFactory(const FObjectInitializer& ObjectInitializer);

	//~ Begin UActorFactory Interface
	virtual bool CanCreateActorFrom(const FAssetData& AssetData, FText& OutErrorMsg) override;
	virtual void PostSpawnActor(UObject* NewAsset, AActor* NewActor) override;
	//~ End UActorFactory Interface
};
