// Copyright ZhaoZining. All Rights Reserved.

#include "MotionWeaverEditor.h"

#include "Pose/Assets/MotionWeaverPoseAssetTypeActions.h"
#include "Pose/Extraction/MotionWeaverPoseToolbar.h"

#include "AssetToolsModule.h"
#include "IAssetTools.h"
#include "Modules/ModuleManager.h"

namespace
{
	/** 内容浏览器里注册过的资产类型动作，关闭时按同一批引用撤销。 */
	TArray<TSharedRef<FMotionWeaverPoseAssetTypeActions>> RegisteredAssetTypeActions;
}

void FMotionWeaverEditorModule::StartupModule()
{
	// 资产类型动作：让姿势代理资产在内容浏览器里有图标、分类与拖放能力。
	// 用 LoadModuleChecked 而不是先判断已加载：跳过注册会让功能凭空消失且不留痕迹。
	IAssetTools& AssetTools = FModuleManager::LoadModuleChecked<FAssetToolsModule>("AssetTools").Get();

	const TSharedRef<FMotionWeaverPoseAssetTypeActions> PoseAssetActions =
		MakeShared<FMotionWeaverPoseAssetTypeActions>();

	AssetTools.RegisterAssetTypeActions(PoseAssetActions);
	RegisteredAssetTypeActions.Add(PoseAssetActions);

	// 工具栏入口：动画编辑器里的「抽取姿势代理」。
	FMotionWeaverPoseToolbar::Register();
}

void FMotionWeaverEditorModule::ShutdownModule()
{
	FMotionWeaverPoseToolbar::Unregister();

	if (FModuleManager::Get().IsModuleLoaded("AssetTools"))
	{
		IAssetTools& AssetTools = FModuleManager::GetModuleChecked<FAssetToolsModule>("AssetTools").Get();

		// 数组存的就是 TSharedRef，范围声明跟着写 TSharedRef，避免每轮构造临时 TSharedPtr。
		for (const TSharedRef<FMotionWeaverPoseAssetTypeActions>& Actions : RegisteredAssetTypeActions)
		{
			AssetTools.UnregisterAssetTypeActions(Actions);
		}
	}

	RegisteredAssetTypeActions.Reset();
}

IMPLEMENT_MODULE(FMotionWeaverEditorModule, MotionWeaverEditor)
