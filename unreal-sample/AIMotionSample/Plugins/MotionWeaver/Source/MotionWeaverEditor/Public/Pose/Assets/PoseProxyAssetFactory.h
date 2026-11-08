// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "Factories/Factory.h"
#include "PoseProxyAssetFactory.generated.h"

/**
 * 内容浏览器「神经网络 → 姿态代理」的创建入口。
 *
 * 注意与 PoseActorFactory 的区别：
 *  - 这个造的是**资产**（UDataAsset，一帧姿态的数据本体）；
 *  - PoseActorFactory 造的是**关卡里的 Actor**（把资产摆进场景的那个壳）。
 * 两者名字接近但职责完全不同，别混用。
 *
 * 新建出来的资产是空的：姿态数据要靠动画编辑器里的「抽取姿态代理」从暂停帧灌进来。
 * 仍然需要这个入口，是因为用户要能先占位、先组织目录，再回头填内容。
 */
UCLASS(HideCategories = Object, MinimalAPI)
class UPoseProxyAssetFactory final : public UFactory
{
	GENERATED_BODY()

public:
	UPoseProxyAssetFactory();

	//~ Begin UFactory Interface
	virtual bool CanCreateNew() const override { return true; }
	virtual uint32 GetMenuCategories() const override;
	virtual FText GetDisplayName() const override;
	virtual FText GetToolTip() const override;
	virtual UObject* FactoryCreateNew(
		UClass* InClass, UObject* InParent, FName InName, EObjectFlags Flags,
		UObject* Context, FFeedbackContext* Warn) override;
	//~ End UFactory Interface
};
