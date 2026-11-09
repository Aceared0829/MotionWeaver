// Copyright ZhaoZining. All Rights Reserved.

#include "MotionWeaverEditor.h"

#include "Pose/Assets/PoseProxyAssetTypeActions.h"
#include "Pose/Assets/PoseGroupProxyAssetTypeActions.h"
#include "Pose/Extraction/PoseToolbar.h"

#include "AssetToolsModule.h"
#include "IAssetTools.h"
#include "Modules/ModuleManager.h"

namespace
{
	/**
	 * 内容浏览器里注册过的资产类型动作，关闭时按同一批引用撤销。
	 * 用基类指针存：两种资产的动作类不同，但撤销接口是同一套。
	 */
	TArray<TSharedPtr<FAssetTypeActions_Base>> RegisteredAssetTypeActions;
}

void FMotionWeaverEditorModule::StartupModule()
{
	// 资产类型动作：让姿态代理资产在内容浏览器里有图标、分类与拖放能力。
	// 用 LoadModuleChecked 而不是先判断已加载：跳过注册会让功能凭空消失且不留痕迹。
	IAssetTools& AssetTools = FModuleManager::LoadModuleChecked<FAssetToolsModule>("AssetTools").Get();

	// 新建菜单的二级分组由 TypeActions::GetSubMenus() 提供，
	// 引擎会把它和 Factory 的一级分类合成「动画 → 神经网络」。
	// 不用自己挂菜单：之前试过 GetAllAssetContextMenuExtenders + AddSubMenu，
	// 结果是入口落在顶部「创建」区，与「新建文件夹」并列，位置不对。

	const TSharedRef<FPoseProxyAssetTypeActions> PoseAssetActions = MakeShared<FPoseProxyAssetTypeActions>();
	AssetTools.RegisterAssetTypeActions(PoseAssetActions);
	RegisteredAssetTypeActions.Add(PoseAssetActions);

	// 姿态代理组：跑酷这类复合运动的容器资产，与单帧姿态代理并列。
	const TSharedRef<FPoseGroupProxyAssetTypeActions> PoseGroupAssetActions =
		MakeShared<FPoseGroupProxyAssetTypeActions>();
	AssetTools.RegisterAssetTypeActions(PoseGroupAssetActions);
	RegisteredAssetTypeActions.Add(PoseGroupAssetActions);

	// 工具栏入口：动画编辑器里的「抽取姿态代理」。
	FPoseToolbar::Register();

	// 关卡侧不再有任何编辑入口：不注册组件可视化器，也不接管视口拖放。
	//
	// 曾经试过两条路，都放弃了：
	//  1. 组件可视化器 + 命中球：组件选中被 CanCreateBlueprintOfClass 挡死
	//     （链 Actor 是 final + BlueprintType），点身体永远出不来 gizmo；
	//  2. per-key 代理 Actor：能点能拖，但一次拖动会触发全链重算，
	//     而且把「编辑」放在了最不该放的地方——关卡。
	// 现在所有编辑都在资产编辑器里做（见 FPoseGroupProxyAssetEditor）。
}

void FMotionWeaverEditorModule::ShutdownModule()
{
	FPoseToolbar::Unregister();

	if (FModuleManager::Get().IsModuleLoaded("AssetTools"))
	{
		IAssetTools& AssetTools = FModuleManager::GetModuleChecked<FAssetToolsModule>("AssetTools").Get();

		for (const TSharedPtr<FAssetTypeActions_Base>& Actions : RegisteredAssetTypeActions)
		{
			if (Actions.IsValid())
			{
				AssetTools.UnregisterAssetTypeActions(Actions.ToSharedRef());
			}
		}
	}

	RegisteredAssetTypeActions.Reset();
}

IMPLEMENT_MODULE(FMotionWeaverEditorModule, MotionWeaverEditor)
