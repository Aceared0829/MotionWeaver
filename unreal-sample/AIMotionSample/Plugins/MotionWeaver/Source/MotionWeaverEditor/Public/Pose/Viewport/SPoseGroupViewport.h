// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "AdvancedPreviewScene.h"
#include "Pose/Proxy/PoseChainTypes.h"
#include "SAssetEditorViewport.h"
#include "UObject/GCObject.h"

class UPoseGroupProxyAsset;
class UPoseComponent;

/**
 * 姿态代理组的预览视口。
 *
 * 持有一个独立预览场景：只显示「当前选中帧」的真蒙皮，
 * 其余帧与轨迹由 FPoseGroupViewportClient 直接画线。
 */
class SPoseGroupViewport final
	: public SAssetEditorViewport
	, public FGCObject
{
public:
	SLATE_BEGIN_ARGS(SPoseGroupViewport) {}
		SLATE_ARGUMENT(UPoseGroupProxyAsset*, GroupAsset)
	SLATE_END_ARGS()

	void Construct(const FArguments& InArgs);

	/** 换资产。 */
	void SetGroupAsset(UPoseGroupProxyAsset* InAsset);

	/** 切到某一帧并刷新蒙皮。 */
	void SetSelectedKey(int32 KeyIndex);

	/** 视口里拖拽产生的改动。由编辑器接走，避免控件直接改资产。 */
	DECLARE_DELEGATE_OneParam(FOnPoseKeyDragged, const FPoseKey& /*NewKeyValue*/);
	FOnPoseKeyDragged OnKeyDragged;

	/** 拖拽开始/结束，供编辑器开合事务。 */
	DECLARE_DELEGATE(FOnPoseDragStateChanged);
	FOnPoseDragStateChanged OnDragStarted;
	FOnPoseDragStateChanged OnDragFinished;

	//~ Begin FGCObject Interface
	virtual void AddReferencedObjects(FReferenceCollector& Collector) override;
	virtual FString GetReferencerName() const override { return TEXT("SPoseGroupViewport"); }
	//~ End FGCObject Interface

protected:
	//~ Begin SEditorViewport Interface
	virtual TSharedRef<FEditorViewportClient> MakeEditorViewportClient() override;
	//~ End SEditorViewport Interface

private:
	/** 把当前选中帧的姿态灌进预览组件。 */
	void ApplySelectedPose();

	/** 拖动过程中的即时预览：只动组件，不碰资产。 */
	void ApplyDragPreview(const FPoseKey& NewKeyValue);

	void HandleKeyDragged(const FPoseKey& NewKeyValue);
	void HandleDragStarted();
	void HandleDragFinished();

	TSharedPtr<FAdvancedPreviewScene> PreviewScene;
	TObjectPtr<UPoseComponent> PreviewComponent;

	/** 裸指针：FGCObject 已经登记了它，这里只需要能读。 */
	UPoseGroupProxyAsset* GroupAsset = nullptr;
	int32 SelectedKeyIndex = INDEX_NONE;
};
