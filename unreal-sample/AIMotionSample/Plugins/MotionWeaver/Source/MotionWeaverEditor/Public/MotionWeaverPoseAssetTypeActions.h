// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "AssetTypeActions_Base.h"

class UMotionWeaverPoseAsset;

/**
 * 姿势代理资产在内容浏览器中的表现：图标、分类、双击行为。
 *
 * 不提供"打开编辑器"——这类资产没有独立编辑界面，所有编辑都在细节面板完成。
 */
class FMotionWeaverPoseAssetTypeActions final : public FAssetTypeActions_Base
{
public:
	//~ Begin IAssetTypeActions Interface
	virtual FText GetName() const override;
	virtual FColor GetTypeColor() const override;
	virtual UClass* GetSupportedClass() const override;
	virtual uint32 GetCategories() override;
	virtual void OpenAssetEditor(const TArray<UObject*>& InObjects, TSharedPtr<IToolkitHost> EditWithinLevelEditor) override;
	//~ End IAssetTypeActions Interface
};
