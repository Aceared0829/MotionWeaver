// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Assets/MotionWeaverPoseGroupAssetTypeActions.h"

#include "Pose/MotionWeaverPoseGroupAsset.h"

#include "AssetTypeCategories.h"

#define LOCTEXT_NAMESPACE "MotionWeaverPoseGroupAssetTypeActions"

FText FMotionWeaverPoseGroupAssetTypeActions::GetName() const
{
	return LOCTEXT("Name", "MotionWeaver 姿势代理组");
}

FColor FMotionWeaverPoseGroupAssetTypeActions::GetTypeColor() const
{
	// 橙色：与单帧姿势代理的蓝色区分开，一眼看出这是「一段运动」而不是「一帧」。
	return FColor(255, 170, 60);
}

UClass* FMotionWeaverPoseGroupAssetTypeActions::GetSupportedClass() const
{
	return UMotionWeaverPoseGroupAsset::StaticClass();
}

uint32 FMotionWeaverPoseGroupAssetTypeActions::GetCategories()
{
	return EAssetTypeCategories::Animation;
}

const TArray<FText>& FMotionWeaverPoseGroupAssetTypeActions::GetSubMenus() const
{
	// 与姿势代理同一个二级菜单名，两项并排。
	static const TArray<FText> SubMenus
	{
		LOCTEXT("MotionWeaverSubMenu", "神经网络")
	};
	return SubMenus;
}

#undef LOCTEXT_NAMESPACE
