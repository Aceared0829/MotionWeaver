// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "AssetTypeActions_Base.h"

/**
 * 姿势代理组资产在内容浏览器里的表现。
 * 没有独立编辑界面：所有配置都在细节面板完成。
 */
class FPoseGroupProxyAssetTypeActions final : public FAssetTypeActions_Base
{
public:
	//~ Begin IAssetTypeActions Interface
	virtual FText GetName() const override;
	virtual FColor GetTypeColor() const override;
	virtual UClass* GetSupportedClass() const override;
	virtual uint32 GetCategories() override;
	virtual const TArray<FText>& GetSubMenus() const override;
	virtual bool HasActions(const TArray<UObject*>& InObjects) const override { return false; }
	//~ End IAssetTypeActions Interface
};
