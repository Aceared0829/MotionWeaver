// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Assets/TypeActions/PoseProxyAssetTypeActions.h"

#include "Pose/Data/PoseProxyAsset.h"
#include "Pose/Assets/Editors/PoseProxyAssetEditor.h"

#include "Toolkits/IToolkitHost.h"

#define LOCTEXT_NAMESPACE "PoseProxyAssetTypeActions"

FText FPoseProxyAssetTypeActions::GetName() const
{
	return LOCTEXT("PoseAssetName", "MotionWeaver Pose");
}

FColor FPoseProxyAssetTypeActions::GetTypeColor() const
{
	return FColor(120, 180, 255);
}

UClass* FPoseProxyAssetTypeActions::GetSupportedClass() const
{
	return UPoseProxyAsset::StaticClass();
}

uint32 FPoseProxyAssetTypeActions::GetCategories()
{
	// 放在「动画」下：用户明确要求与动画蓝图同级。
	return EAssetTypeCategories::Animation;
}

const TArray<FText>& FPoseProxyAssetTypeActions::GetSubMenus() const
{
	// 二级菜单名。与姿态代理组共用同一个名字，两项会并排落在
	// 「动画 → 神经网络」里，而不是散落在「基本属性」。
	static const TArray<FText> SubMenus
	{
		LOCTEXT("PoseChainSubMenu", "神经网络")
	};
	return SubMenus;
}

void FPoseProxyAssetTypeActions::OpenAssetEditor(
	const TArray<UObject*>& InObjects, TSharedPtr<IToolkitHost> EditWithinLevelEditor)
{
	// 双击资产就直接进带视口的编辑器，不必先拖进关卡。
	const EToolkitMode::Type Mode =
		EditWithinLevelEditor.IsValid() ? EToolkitMode::WorldCentric : EToolkitMode::Standalone;

	for (UObject* Object : InObjects)
	{
		if (UPoseProxyAsset* PoseAsset = Cast<UPoseProxyAsset>(Object))
		{
			const TSharedRef<FPoseProxyAssetEditor> NewEditor =
				MakeShared<FPoseProxyAssetEditor>();
			NewEditor->InitPoseProxyAssetEditor(Mode, EditWithinLevelEditor, PoseAsset);
		}
	}
}

#undef LOCTEXT_NAMESPACE
