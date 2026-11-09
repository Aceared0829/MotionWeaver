// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "EditorViewportClient.h"
#include "Pose/Proxy/PoseChainTypes.h"

class UPoseGroupProxyAsset;
class FAdvancedPreviewScene;

/**
 * 姿态代理组编辑器的视口客户端。
 *
 * 除了画整段运动（当前帧真蒙皮、其余帧骨架幽灵、求解轨迹与朝向箭头），
 * 它还提供标准的引擎手柄（FWidget）：平移三轴、旋转。
 *
 * 用 FWidget 而不是自己算屏幕拖拽，是因为它是 FEditorViewportClient 自带的
 * 成员，任何派生视口都能用，不依赖关卡的选择系统——它属于视口，不属于关卡。
 * 自己实现的话要重复做射线求交、轴锁定、吸附这三件事，而且拿不到标准外观。
 *
 * 三个轴都放开：LocalLocation 是 FVector，求解器全程按三维 Hermite 插值，
 * 不限制 Z。唯一需要留意的是导出给模型时竖直分量怎么处理，那是导出层的事，
 * 不影响编辑器里能摆多高。
 */
class FPoseGroupViewportClient final : public FEditorViewportClient
{
public:
	FPoseGroupViewportClient(FPreviewScene* InPreviewScene, const TSharedRef<SEditorViewport>& InViewport);

	/** 设置要显示的资产。为空时只画空场景。 */
	void SetGroupAsset(UPoseGroupProxyAsset* InAsset);

	/** 当前正在看第几个关键帧。INDEX_NONE 表示没有选中。 */
	void SetSelectedKey(int32 InKeyIndex);

	int32 GetSelectedKey() const { return SelectedKeyIndex; }

	/**
	 * 手柄拖动结果回调。由编辑器注入，避免视口客户端直接改资产——
	 * 资产改动要走事务、要触发求解、要让时间轴跟着刷新，那些都在编辑器里。
	 */
	DECLARE_DELEGATE_OneParam(FOnKeyDragged, const FPoseKey& /*NewKeyValue*/);
	void SetOnKeyDragged(FOnKeyDragged InDelegate) { OnKeyDragged = InDelegate; }

	/** 拖拽开始/结束，供编辑器开合事务。 */
	DECLARE_DELEGATE(FOnDragStateChanged);
	void SetOnDragStarted(FOnDragStateChanged InDelegate) { OnDragStarted = InDelegate; }
	void SetOnDragFinished(FOnDragStateChanged InDelegate) { OnDragFinished = InDelegate; }

	//~ Begin FEditorViewportClient Interface
	virtual void Draw(const FSceneView* View, FPrimitiveDrawInterface* PDI) override;
	virtual FVector GetWidgetLocation() const override;
	virtual bool InputWidgetDelta(FViewport* InViewport, EAxisList::Type CurrentAxis,
		FVector& Drag, FRotator& Rot, FVector& Scale) override;
	virtual void TrackingStarted(const struct FInputEventState& InInputState, bool bIsDraggingWidget, bool bNudge) override;
	virtual void TrackingStopped() override;
	//~ End FEditorViewportClient Interface

private:
	/** 画求解出来的轨迹 + 每个关键帧的朝向箭头 + 选中帧的圈。 */
	void DrawTrajectory(FPrimitiveDrawInterface* PDI) const;

	/** 画除当前帧以外的关键帧骨架（幽灵）。 */
	void DrawGhostKeys(FPrimitiveDrawInterface* PDI) const;

	/** 当前选中帧的数据指针；没有选中时返回 nullptr。 */
	const FPoseKey* GetSelectedKeyPtr() const;

	TWeakObjectPtr<UPoseGroupProxyAsset> GroupAsset;

	int32 SelectedKeyIndex = INDEX_NONE;

	/** 拖拽起始值。每次拖动都以它为基准重算，避免逐帧累加攒误差。 */
	FPoseKey DragStartKey;
	bool bHasDragStart = false;

	FOnKeyDragged OnKeyDragged;
	FOnDragStateChanged OnDragStarted;
	FOnDragStateChanged OnDragFinished;
};
