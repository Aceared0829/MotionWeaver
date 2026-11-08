// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "Factories/Factory.h"
#include "MotionWeaverPoseGroupFactory.generated.h"

/**
 * 内容浏览器「动画 → MotionWeaver 姿势代理组」的创建入口。
 *
 * 与单帧姿势代理并列放，因为它们是同一层的两种东西：
 * 一个是「这一帧长什么样」，一个是「这些帧怎么串成一段跑酷」。
 */
UCLASS(HideCategories = Object, MinimalAPI)
class UMotionWeaverPoseGroupFactory final : public UFactory
{
	GENERATED_BODY()

public:
	UMotionWeaverPoseGroupFactory();

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
