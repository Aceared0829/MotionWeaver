// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Assets/MotionWeaverPoseGroupFactory.h"

#include "Pose/MotionWeaverPoseGroupAsset.h"

#include "AssetTypeCategories.h"

#define LOCTEXT_NAMESPACE "MotionWeaverPoseGroupFactory"

UMotionWeaverPoseGroupFactory::UMotionWeaverPoseGroupFactory()
{
	bCreateNew = true;
	bEditAfterNew = true;
	bEditorImport = false;
	SupportedClass = UMotionWeaverPoseGroupAsset::StaticClass();
}

uint32 UMotionWeaverPoseGroupFactory::GetMenuCategories() const
{
	return EAssetTypeCategories::Animation;
}

FText UMotionWeaverPoseGroupFactory::GetDisplayName() const
{
	return LOCTEXT("DisplayName", "MotionWeaver 姿势代理组");
}

FText UMotionWeaverPoseGroupFactory::GetToolTip() const
{
	return LOCTEXT("ToolTip",
		"把一串姿势代理串成一段可复用的复合运动（跑酷、翻越、起身）。");
}

UObject* UMotionWeaverPoseGroupFactory::FactoryCreateNew(
	UClass* InClass, UObject* InParent, FName InName, EObjectFlags Flags,
	UObject* Context, FFeedbackContext* Warn)
{
	return NewObject<UMotionWeaverPoseGroupAsset>(InParent, InClass, InName, Flags);
}

#undef LOCTEXT_NAMESPACE
