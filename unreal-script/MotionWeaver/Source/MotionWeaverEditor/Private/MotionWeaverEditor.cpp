// Copyright ZhaoZining. All Rights Reserved.

#include "MotionWeaverEditor.h"

#include "Pose/Assets/MotionWeaverPoseAssetTypeActions.h"
#include "Pose/Assets/MotionWeaverPoseGroupAssetTypeActions.h"
#include "Pose/Extraction/MotionWeaverPoseToolbar.h"
#include "Pose/Proxy/MotionWeaverPoseChainDragDropHandler.h"

#include "AssetToolsModule.h"
#include "Editor.h"
#include "IAssetTools.h"
#include "LevelEditorDragDropHandler.h"
#include "Modules/ModuleManager.h"

namespace
{
	/**
	 * 内容浏览器里注册过的资产类型动作，关闭时按同一批引用撤销。
	 * 用基类指针存：两种资产的动作类不同，但撤销接口是同一套。
	 */
	TArray<TSharedPtr<FAssetTypeActions_Base>> RegisteredAssetTypeActions;

	/** 视口拖放处理器的创建委托是否已绑定。单播委托没有句柄，只记状态。 */
	bool bPoseChainDragDropHandlerBound = false;

}

void FMotionWeaverEditorModule::StartupModule()
{
	// 资产类型动作：让姿势代理资产在内容浏览器里有图标、分类与拖放能力。
	// 用 LoadModuleChecked 而不是先判断已加载：跳过注册会让功能凭空消失且不留痕迹。
	IAssetTools& AssetTools = FModuleManager::LoadModuleChecked<FAssetToolsModule>("AssetTools").Get();

	// 新建菜单的二级分组由 TypeActions::GetSubMenus() 提供，
	// 引擎会把它和 Factory 的一级分类合成「动画 → 神经网络」。
	// 不用自己挂菜单：之前试过 GetAllAssetContextMenuExtenders + AddSubMenu，
	// 结果是入口落在顶部「创建」区，与「新建文件夹」并列，位置不对。

	const TSharedRef<FMotionWeaverPoseAssetTypeActions> PoseAssetActions = MakeShared<FMotionWeaverPoseAssetTypeActions>();
	AssetTools.RegisterAssetTypeActions(PoseAssetActions);
	RegisteredAssetTypeActions.Add(PoseAssetActions);

	// 姿势代理组：跑酷这类复合运动的容器资产，与单帧姿势代理并列。
	const TSharedRef<FMotionWeaverPoseGroupAssetTypeActions> PoseGroupAssetActions =
		MakeShared<FMotionWeaverPoseGroupAssetTypeActions>();
	AssetTools.RegisterAssetTypeActions(PoseGroupAssetActions);
	RegisteredAssetTypeActions.Add(PoseGroupAssetActions);

	// 工具栏入口：动画编辑器里的「抽取姿势代理」。
	FMotionWeaverPoseToolbar::Register();

	// 姿态链的视口编辑不再走组件可视化器。
	// 旧做法是把一个 10cm 的命中球挂在蒙皮组件上，点身体命中的仍是 HActor，
	// 而组件选中被 CanCreateBlueprintOfClass 挡死（链 Actor 是 final + BlueprintType），
	// 那条路走不通。现在由 AMotionWeaverPoseChainKeyProxy 接管：
	// 每个 key 在编辑器里就是一个真正的 Actor，点它就是点普通 Actor。
	// 因此这里不再注册任何可视化器。

	// 视口拖放：把姿势资产拖到选中的链 Actor 上时，追加一条关键帧，
	// 而不是按默认工厂建一个独立的单帧 Actor。
	if (GEditor != nullptr)
	{
		GEditor->OnCreateLevelEditorDragDropHandler().BindLambda(
			[]() -> ULevelEditorDragDropHandler*
			{
				return NewObject<UMotionWeaverPoseChainDragDropHandler>(
					GEditor, UMotionWeaverPoseChainDragDropHandler::StaticClass());
			});
		bPoseChainDragDropHandlerBound = true;
	}
}

void FMotionWeaverEditorModule::ShutdownModule()
{
	FMotionWeaverPoseToolbar::Unregister();

	if (bPoseChainDragDropHandlerBound && GEditor != nullptr)
	{
		GEditor->OnCreateLevelEditorDragDropHandler().Unbind();
		bPoseChainDragDropHandlerBound = false;

		// 单播委托创建的实例由 GEditor 缓存；解绑后把它清掉，
		// 否则下次 GetLevelEditorDragDropHandler 还会拿到我们那个已失效的对象。
		if (ULevelEditorDragDropHandler* Cached = GEditor->GetLevelEditorDragDropHandler())
		{
			if (Cached->IsA(UMotionWeaverPoseChainDragDropHandler::StaticClass()))
			{
				Cached->MarkAsGarbage();
			}
		}
	}

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
