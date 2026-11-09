// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "Toolkits/AssetEditorToolkit.h"

class UPoseGroupProxyAsset;
class SPoseGroupViewport;
class SPoseKeyTimeline;
class IDetailsView;
class SDockTab;

/**
 * 姿态代理组的编辑器：左边视口看姿态与轨迹，中间时间轴排帧，右边细节面板。
 *
 * 这是 MotionWeaver 唯一的编辑入口。关卡里的 APoseGroupActor 只读不写。
 *
 * 为什么编辑放在这里而不是关卡：
 *  - 关卡里要拿 gizmo 就得跟引擎的 Actor 选择系统打交道，而组件选中
 *    被 CanCreateBlueprintOfClass 挡死（Actor 是 final + BlueprintType）；
 *  - 这里视口是自己实现的，命中测试、拖拽、选择全由自己掌控。
 */
class FPoseGroupProxyAssetEditor final : public FAssetEditorToolkit
{
public:
	/** 打开指定资产的编辑器。 */
	void InitPoseGroupProxyAssetEditor(
		const EToolkitMode::Type Mode,
		const TSharedPtr<IToolkitHost>& InitToolkitHost,
		UPoseGroupProxyAsset* InAsset);

	/** 当前选中的关键帧下标（时间轴与视口共用）。 */
	int32 GetSelectedKeyIndex() const { return SelectedKeyIndex; }

	/**
	 * 切换当前编辑的关键帧。
	 * 视口、时间轴、细节面板三方都跟着走，避免出现「视口显示第 2 帧
	 * 但时间轴高亮第 0 帧」这种各说各话的状态。
	 */
	void SetSelectedKeyIndex(int32 InKeyIndex);

	/** 资产内容变了（增删帧、改帧号）之后，让三方重新读一次。 */
	void RefreshFromAsset();

	/** 时间轴要读资产。 */
	UPoseGroupProxyAsset* GetAsset() const { return GroupAsset; }

	//~ Begin FAssetEditorToolkit Interface
	virtual void RegisterTabSpawners(const TSharedRef<FTabManager>& InTabManager) override;
	virtual void UnregisterTabSpawners(const TSharedRef<FTabManager>& InTabManager) override;
	virtual FName GetToolkitFName() const override;
	virtual FText GetBaseToolkitName() const override;
	virtual FText GetToolkitName() const override;
	virtual FString GetWorldCentricTabPrefix() const override;
	virtual FLinearColor GetWorldCentricTabColorScale() const override;
	//~ End FAssetEditorToolkit Interface

	/** 往当前资产追加一个空关键帧，帧号接在最后一个之后。 */
	void AddKey();

	/** 删除选中的关键帧。 */
	void DeleteSelectedKey();

	/** 把选中帧的帧号改动写回资产并重解。 */
	void SetSelectedKeyFrame(int32 NewFrame);

private:
	TSharedRef<SDockTab> SpawnTab_Viewport(const FSpawnTabArgs& Args);
	TSharedRef<SDockTab> SpawnTab_Timeline(const FSpawnTabArgs& Args);
	TSharedRef<SDockTab> SpawnTab_Details(const FSpawnTabArgs& Args);

	static const FName ViewportTabId;
	static const FName TimelineTabId;
	static const FName DetailsTabId;

	UPoseGroupProxyAsset* GroupAsset = nullptr;

	TSharedPtr<SPoseGroupViewport> ViewportWidget;
	TSharedPtr<SPoseKeyTimeline> TimelineWidget;
	TSharedPtr<IDetailsView> DetailsView;

	int32 SelectedKeyIndex = INDEX_NONE;
};
