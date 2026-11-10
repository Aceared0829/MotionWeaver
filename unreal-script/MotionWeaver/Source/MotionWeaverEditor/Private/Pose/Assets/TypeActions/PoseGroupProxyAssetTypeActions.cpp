// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Assets/TypeActions/PoseGroupProxyAssetTypeActions.h"

#include "Pose/Assets/Editors/PoseGroupProxyAssetEditor.h"
#include "Pose/Data/PoseGroupProxyAsset.h"

#include "AssetTypeCategories.h"
#include "Toolkits/IToolkitHost.h"

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

void FPoseGroupProxyAssetTypeActions::OpenAssetEditor(
	const TArray<UObject*>& InObjects, TSharedPtr<IToolkitHost> EditWithinLevelEditor)
{
	// 双击资产直接进带视口的编辑器，不必先拖进关卡。
	const EToolkitMode::Type Mode =
		EditWithinLevelEditor.IsValid() ? EToolkitMode::WorldCentric : EToolkitMode::Standalone;

	for (UObject* Object : InObjects)
	{
		if (UPoseGroupProxyAsset* GroupAsset = Cast<UPoseGroupProxyAsset>(Object))
		{
			const TSharedRef<FPoseGroupProxyAssetEditor> NewEditor = MakeShared<FPoseGroupProxyAssetEditor>();
			NewEditor->InitPoseGroupProxyAssetEditor(Mode, EditWithinLevelEditor, GroupAsset);
		}
	}
}

#undef LOCTEXT_NAMESPACE
