// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Assets/PoseGroupProxyAssetTypeActions.h"

#include "Pose/PoseGroupProxyAsset.h"

#include "AssetTypeCategories.h"

#define LOCTEXT_NAMESPACE "PoseGroupProxyAssetTypeActions"

FText FPoseGroupProxyAssetTypeActions::GetName() const
{
	return LOCTEXT("Name", "姿态代理组");
}

FColor FPoseGroupProxyAssetTypeActions::GetTypeColor() const
{
	// 橙色：与单帧姿态代理的蓝色区分开，一眼看出这是「一段运动」而不是「一帧」。
	return FColor(255, 170, 60);
}

UClass* FPoseGroupProxyAssetTypeActions::GetSupportedClass() const
{
	return UPoseGroupProxyAsset::StaticClass();
}

uint32 FPoseGroupProxyAssetTypeActions::GetCategories()
{
	return EAssetTypeCategories::Animation;
}

const TArray<FText>& FPoseGroupProxyAssetTypeActions::GetSubMenus() const
{
	// 与姿态代理同一个二级菜单名，两项并排。
	static const TArray<FText> SubMenus
	{
		LOCTEXT("PoseChainSubMenu", "神经网络")
	};
	return SubMenus;
}

#undef LOCTEXT_NAMESPACE
