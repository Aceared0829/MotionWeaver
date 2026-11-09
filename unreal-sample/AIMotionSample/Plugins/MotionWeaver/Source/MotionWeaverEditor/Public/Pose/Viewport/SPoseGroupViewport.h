// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "AdvancedPreviewScene.h"
#include "Pose/Proxy/PoseChainTypes.h"
#include "SAssetEditorViewport.h"
#include "UObject/GCObject.h"

class UPoseGroupProxyAsset;
class UPoseComponent;
class FPoseGroupViewportClient;

/**
 * 姿态代理组的预览视口。
 *
 * 持有一个独立预览场景：只显示「当前选中帧」的真蒙皮，
 * 其余帧与轨迹由 FPoseGroupViewportClient 直接画线。
 * 三轴手柄由 FPoseGroupEditMode 提供——那是引擎给资产编辑器留的位置。
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

	/** 拖拽结束：松手时才求解。 */
	DECLARE_DELEGATE(FOnPoseDragFinished);
	FOnPoseDragFinished OnDragFinished;

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

	/** 取本视口的客户端，未就绪时返回 nullptr。 */
	FPoseGroupViewportClient* GetPoseViewportClient() const;

	void HandleKeyDragged(const FPoseKey& NewKeyValue);
	void HandleDragFinished();

	TSharedPtr<FAdvancedPreviewScene> PreviewScene;
	TObjectPtr<UPoseComponent> PreviewComponent;

	/** 裸指针：FGCObject 已经登记了它，这里只需要能读。 */
	UPoseGroupProxyAsset* GroupAsset = nullptr;

	int32 SelectedKeyIndex = INDEX_NONE;
};
