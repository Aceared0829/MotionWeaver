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
 * 它还负责「拖」：在视口里按住当前关键帧的身体左右拖动，改它的地面位置；
 * 按住 Shift 拖则改朝向。写回只有一处——把结果交给编辑器，
 * 由编辑器统一 Modify + Solve + 刷新，保证撤销栈与三方同步都走同一条路。
 *
 * 不做 3D gizmo 是有意的：gizmo 要走引擎的 widget 与轴锁定，
 * 那是关卡编辑器的地盘；这里需求很具体——改一个平面上的 XY 和一个 Yaw，
 * 屏幕空间拖拽已经够用，而且不依赖任何选择/命中代理机制。
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
	 * 拖拽回调。由编辑器注入，避免视口客户端直接改资产——
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
	virtual bool InputKey(const FInputKeyEventArgs& EventArgs) override;
	virtual void CapturedMouseMove(FViewport* InViewport, int32 InMouseX, int32 InMouseY) override;
	//~ End FEditorViewportClient Interface

private:
	/** 画求解出来的地面轨迹 + 每个关键帧的朝向箭头。 */
	void DrawTrajectory(FPrimitiveDrawInterface* PDI) const;

	/** 画除当前帧以外的关键帧骨架（幽灵）。 */
	void DrawGhostKeys(FPrimitiveDrawInterface* PDI) const;

	/** 屏幕坐标 -> 水平面（Z=0）上的世界点。返回 false 表示射线与地面平行。 */
	bool ScreenToGround(const FViewport* InViewport, int32 ScreenX, int32 ScreenY, FVector& OutWorld);

	/** 当前选中帧的数据指针；没有选中时返回 nullptr。 */
	const FPoseKey* GetSelectedKeyPtr() const;

	/** 正在拖动时把鼠标位置换算成新的 key 值并交出去。 */
	void ApplyDrag(int32 MouseX, int32 MouseY);

	TWeakObjectPtr<UPoseGroupProxyAsset> GroupAsset;

	int32 SelectedKeyIndex = INDEX_NONE;

	/**
	 * 拖拽状态。
	 * bHasDragStart 单独一个标志：FInputKeyEventArgs 不带鼠标坐标，
	 * 按下那一刻求不出地面点，得等第一次 CapturedMouseMove 才补上。
	 */
	bool bDragging = false;
	bool bHasDragStart = false;
	bool bDragRotateMode = false;
	FVector DragStartWorld = FVector::ZeroVector;
	FIntPoint DragStartMouse = FIntPoint::ZeroValue;
	FIntPoint LastMousePos = FIntPoint::ZeroValue;
	FPoseKey DragStartKey;

	FOnKeyDragged OnKeyDragged;
	FOnDragStateChanged OnDragStarted;
	FOnDragStateChanged OnDragFinished;
};
