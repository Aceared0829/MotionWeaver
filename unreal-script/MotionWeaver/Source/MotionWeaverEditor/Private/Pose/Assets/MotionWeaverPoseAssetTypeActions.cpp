// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Assets/MotionWeaverPoseAssetTypeActions.h"

#include "Pose/MotionWeaverPoseAsset.h"
#include "Pose/Assets/MotionWeaverPoseAssetEditor.h"

#include "Toolkits/IToolkitHost.h"

#define LOCTEXT_NAMESPACE "MotionWeaverPoseAssetTypeActions"

FText FMotionWeaverPoseAssetTypeActions::GetName() const
{
	return LOCTEXT("PoseAssetName", "MotionWeaver Pose");
}

FColor FMotionWeaverPoseAssetTypeActions::GetTypeColor() const
{
	return FColor(120, 180, 255);
}

UClass* FMotionWeaverPoseAssetTypeActions::GetSupportedClass() const
{
	return UMotionWeaverPoseAsset::StaticClass();
}

uint32 FMotionWeaverPoseAssetTypeActions::GetCategories()
{
	// 放在「动画」下：用户明确要求与动画蓝图同级。
	return EAssetTypeCategories::Animation;
}

const TArray<FText>& FMotionWeaverPoseAssetTypeActions::GetSubMenus() const
{
	// 二级菜单名。与姿势代理组共用同一个名字，两项会并排落在
	// 「动画 → 神经网络」里，而不是散落在「基本属性」。
	static const TArray<FText> SubMenus
	{
		LOCTEXT("MotionWeaverSubMenu", "神经网络")
	};
	return SubMenus;
}

void FMotionWeaverPoseAssetTypeActions::OpenAssetEditor(
	const TArray<UObject*>& InObjects, TSharedPtr<IToolkitHost> EditWithinLevelEditor)
{
	// 双击资产就直接进带视口的编辑器，不必先拖进关卡。
	const EToolkitMode::Type Mode =
		EditWithinLevelEditor.IsValid() ? EToolkitMode::WorldCentric : EToolkitMode::Standalone;

	for (UObject* Object : InObjects)
	{
		if (UMotionWeaverPoseAsset* PoseAsset = Cast<UMotionWeaverPoseAsset>(Object))
		{
			const TSharedRef<FMotionWeaverPoseAssetEditor> NewEditor =
				MakeShared<FMotionWeaverPoseAssetEditor>();
			NewEditor->InitMotionWeaverPoseAssetEditor(Mode, EditWithinLevelEditor, PoseAsset);
		}
	}
}

#undef LOCTEXT_NAMESPACE
