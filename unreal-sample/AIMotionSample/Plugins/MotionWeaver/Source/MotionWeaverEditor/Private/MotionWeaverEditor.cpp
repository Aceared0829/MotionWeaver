// Copyright ZhaoZining. All Rights Reserved.

#include "MotionWeaverEditor.h"

#include "MotionWeaverPoseAssetTypeActions.h"
#include "MotionWeaverPoseToolbar.h"

#include "AssetToolsModule.h"
#include "IAssetTools.h"
#include "Modules/ModuleManager.h"

#define LOCTEXT_NAMESPACE "FMotionWeaverEditorModule"

namespace
{
	/** 内容浏览器里注册过的资产类型动作，关闭时按同一批句柄撤销。 */
	TArray<TSharedRef<FMotionWeaverPoseAssetTypeActions>> RegisteredAssetTypeActions;
}

void FMotionWeaverEditorModule::StartupModule()
{
	// 资产类型动作：让姿势代理资产在内容浏览器里有图标、分类与拖放能力。
	if (FModuleManager::Get().IsModuleLoaded("AssetTools"))
	{
		IAssetTools& AssetTools = FModuleManager::GetModuleChecked<FAssetToolsModule>("AssetTools").Get();

		TSharedRef<FMotionWeaverPoseAssetTypeActions> PoseAssetActions =
			MakeShared<FMotionWeaverPoseAssetTypeActions>();
		AssetTools.RegisterAssetTypeActions(PoseAssetActions);
		RegisteredAssetTypeActions.Add(PoseAssetActions);
	}

	// 工具栏入口：动画编辑器里的「抽取姿势代理」。
	FMotionWeaverPoseToolbar::Register();
}

void FMotionWeaverEditorModule::ShutdownModule()
{
	FMotionWeaverPoseToolbar::Unregister();

	if (FModuleManager::Get().IsModuleLoaded("AssetTools"))
	{
		IAssetTools& AssetTools = FModuleManager::GetModuleChecked<FAssetToolsModule>("AssetTools").Get();
		for (const TSharedPtr<FMotionWeaverPoseAssetTypeActions>& Actions : RegisteredAssetTypeActions)
		{
			if (Actions.IsValid())
			{
				AssetTools.UnregisterAssetTypeActions(Actions.ToSharedRef());
			}
		}
	}

	RegisteredAssetTypeActions.Reset();
}

#undef LOCTEXT_NAMESPACE

IMPLEMENT_MODULE(FMotionWeaverEditorModule, MotionWeaverEditor)
