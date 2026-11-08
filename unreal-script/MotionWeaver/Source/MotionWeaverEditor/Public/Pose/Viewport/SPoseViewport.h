// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "AdvancedPreviewScene.h"
#include "SAssetEditorViewport.h"
#include "UObject/GCObject.h"

class UPoseProxyAsset;
class UPoseComponent;

/**
 * 姿势资产的预览视口。
 *
 * 内部持有一个独立预览场景，把资产的姿态灌进一个 PoseableMeshComponent 显示，
 * 这样资产本身不用进关卡就能看到姿态。
 */
class SPoseViewport final
	: public SAssetEditorViewport
	, public FGCObject
{
public:
	SLATE_BEGIN_ARGS(SPoseViewport) {}
		/** 要显示的姿势资产。 */
		SLATE_ARGUMENT(UPoseProxyAsset*, PoseAsset)
	SLATE_END_ARGS()

	void Construct(const FArguments& InArgs);

	/** 换一份资产并刷新显示。 */
	void SetPoseAsset(UPoseProxyAsset* InPoseAsset);

	/** 重新从资产读一次姿态。 */
	void RefreshPreview();

	//~ Begin FGCObject Interface
	virtual void AddReferencedObjects(FReferenceCollector& Collector) override;
	virtual FString GetReferencerName() const override { return TEXT("SPoseViewport"); }
	//~ End FGCObject Interface

	/** 供工具箱登记 tab 时使用。 */
	static const FName ViewportTabId;

protected:
	//~ Begin SEditorViewport Interface
	virtual TSharedRef<FEditorViewportClient> MakeEditorViewportClient() override;
	//~ End SEditorViewport Interface

private:
	/** 独立预览场景，生命周期跟着这个控件。 */
	TSharedPtr<FAdvancedPreviewScene> PreviewScene;

	/** 显示姿态的组件，挂在预览场景里。 */
	TObjectPtr<UPoseComponent> PreviewComponent;

	/** 当前显示的资产。 */
	TObjectPtr<UPoseProxyAsset> PoseAsset;
};
