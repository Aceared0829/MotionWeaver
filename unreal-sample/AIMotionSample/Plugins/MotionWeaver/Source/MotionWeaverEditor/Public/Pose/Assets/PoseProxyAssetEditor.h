// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "Toolkits/AssetEditorToolkit.h"

class UPoseProxyAsset;
class SPoseViewport;
class IDetailsView;
class SDockTab;

/**
 * 姿势资产的编辑器：左边视口看姿态，右边细节面板看数据。
 *
 * 双击资产就打开它，不用先把资产拖进关卡。
 */
class FPoseProxyAssetEditor final : public FAssetEditorToolkit
{
public:
	/** 打开指定资产的编辑器。 */
	void InitPoseProxyAssetEditor(
		const EToolkitMode::Type Mode,
		const TSharedPtr<IToolkitHost>& InitToolkitHost,
		UPoseProxyAsset* InPoseAsset);

	//~ Begin FAssetEditorToolkit Interface
	virtual void RegisterTabSpawners(const TSharedRef<FTabManager>& InTabManager) override;
	virtual void UnregisterTabSpawners(const TSharedRef<FTabManager>& InTabManager) override;
	virtual FName GetToolkitFName() const override;
	virtual FText GetBaseToolkitName() const override;
	virtual FText GetToolkitName() const override;
	virtual FString GetWorldCentricTabPrefix() const override;
	virtual FLinearColor GetWorldCentricTabColorScale() const override;
	//~ End FAssetEditorToolkit Interface

private:
	/** tab 的内容构造。 */
	TSharedRef<SDockTab> SpawnTab_Viewport(const FSpawnTabArgs& Args);
	TSharedRef<SDockTab> SpawnTab_Details(const FSpawnTabArgs& Args);

	/** 两个 tab 的固定 id。 */
	static const FName ViewportTabId;
	static const FName DetailsTabId;

	/** 当前编辑的资产。 */
	TObjectPtr<UPoseProxyAsset> PoseAsset;

	/** 视口控件。 */
	TSharedPtr<SPoseViewport> ViewportWidget;

	/** 细节面板。 */
	TSharedPtr<IDetailsView> DetailsView;
};
