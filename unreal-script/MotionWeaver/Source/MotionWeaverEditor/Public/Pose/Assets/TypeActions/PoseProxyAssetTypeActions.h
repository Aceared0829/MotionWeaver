// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "AssetTypeActions_Base.h"

class UPoseProxyAsset;

/**
 * 姿态代理资产在内容浏览器中的表现：图标、分类、双击行为。
 *
 * 不提供"打开编辑器"——这类资产没有独立编辑界面，所有编辑都在细节面板完成。
 */
class FPoseProxyAssetTypeActions final : public FAssetTypeActions_Base
{
public:
	//~ Begin IAssetTypeActions Interface
	virtual FText GetName() const override;
	virtual FColor GetTypeColor() const override;
	virtual UClass* GetSupportedClass() const override;
	virtual uint32 GetCategories() override;
	/**
	 * 二级分组名。引擎把「一级分类 + 这里的文本」合成为「动画 → 神经网络」。
	 * 参考引擎自己的做法：AssetTypeActions_AnimBoneCompressionSettings::GetSubMenus
	 * 用同一套机制给出「动画 → 高级」。
	 */
	virtual const TArray<FText>& GetSubMenus() const override;
	virtual void OpenAssetEditor(const TArray<UObject*>& InObjects, TSharedPtr<IToolkitHost> EditWithinLevelEditor) override;
	//~ End IAssetTypeActions Interface
};
