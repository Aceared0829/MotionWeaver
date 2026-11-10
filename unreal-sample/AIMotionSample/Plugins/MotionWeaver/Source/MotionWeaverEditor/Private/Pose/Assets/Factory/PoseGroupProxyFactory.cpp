// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Assets/Factory/PoseGroupProxyFactory.h"

#include "Pose/Data/PoseGroupProxyAsset.h"

#include "AssetTypeCategories.h"

#define LOCTEXT_NAMESPACE "PoseGroupProxyFactory"

UPoseGroupProxyFactory::UPoseGroupProxyFactory()
{
	bCreateNew = true;
	bEditAfterNew = true;
	bEditorImport = false;
	SupportedClass = UPoseGroupProxyAsset::StaticClass();
}

uint32 UPoseGroupProxyFactory::GetMenuCategories() const
{
	return EAssetTypeCategories::Animation;
}

FText UPoseGroupProxyFactory::GetDisplayName() const
{
	return LOCTEXT("DisplayName", "姿态代理组");
}

FText UPoseGroupProxyFactory::GetToolTip() const
{
	return LOCTEXT("ToolTip",
		"把一串姿态代理串成一段可复用的复合运动（跑酷、翻越、起身）。");
}

UObject* UPoseGroupProxyFactory::FactoryCreateNew(
	UClass* InClass, UObject* InParent, FName InName, EObjectFlags Flags,
	UObject* Context, FFeedbackContext* Warn)
{
	return NewObject<UPoseGroupProxyAsset>(InParent, InClass, InName, Flags);
}

#undef LOCTEXT_NAMESPACE
