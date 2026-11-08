// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Assets/MotionWeaverPoseAssetFactory.h"

#include "AssetTypeCategories.h"
#include "Pose/MotionWeaverPoseAsset.h"

#define LOCTEXT_NAMESPACE "MotionWeaverPoseAssetFactory"

UMotionWeaverPoseAssetFactory::UMotionWeaverPoseAssetFactory()
{
	bCreateNew = true;
	bEditAfterNew = true;
	bEditorImport = false;
	SupportedClass = UMotionWeaverPoseAsset::StaticClass();
}

uint32 UMotionWeaverPoseAssetFactory::GetMenuCategories() const
{
	// 一级分类「动画」。二级由对应 TypeActions 的 GetSubMenus() 给出（「神经网络」）。
	// 两者由引擎合成为「动画 → 神经网络」，不需要自己挂菜单。
	return EAssetTypeCategories::Animation;
}

FText UMotionWeaverPoseAssetFactory::GetDisplayName() const
{
	return LOCTEXT("DisplayName", "MotionWeaver 姿势代理");
}

FText UMotionWeaverPoseAssetFactory::GetToolTip() const
{
	return LOCTEXT("ToolTip",
		"新建一帧姿态资产。内容可用动画编辑器里的「抽取姿势代理」从暂停帧灌入。");
}

UObject* UMotionWeaverPoseAssetFactory::FactoryCreateNew(
	UClass* InClass, UObject* InParent, FName InName, EObjectFlags Flags,
	UObject* Context, FFeedbackContext* Warn)
{
	return NewObject<UMotionWeaverPoseAsset>(InParent, InClass, InName, Flags);
}

#undef LOCTEXT_NAMESPACE
