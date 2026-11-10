// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/PoseGroupEditMode.h"

#include "Pose/Data/PoseGroupProxyAsset.h"
#include "Pose/Solve/PoseChainTypes.h"

#include "Editor.h"
#include "EditorModeManager.h"
#include "EditorViewportClient.h"

const FEditorModeID PoseGroupEditModeID(TEXT("PoseGroupEditMode"));

FPoseGroupEditMode::FPoseGroupEditMode()
{
	// 网格由视口自己画（DrawHelper.bDrawGrid），模式里再画一遍会重复。
	bDrawGrid = false;
	bDrawPivot = false;
}

void FPoseGroupEditMode::SetEditingContext(
	UPoseGroupProxyAsset* InAsset, USceneComponent* InPreviewComponent, int32 InSelectedKey)
{
	GroupAsset = InAsset;
	PreviewComponent = InPreviewComponent;
	SelectedKeyIndex = InSelectedKey;
}

bool FPoseGroupEditMode::CanManipulate() const
{
	return GroupAsset.IsValid()
		&& PreviewComponent != nullptr
		&& GroupAsset->Keys.IsValidIndex(SelectedKeyIndex);
}

bool FPoseGroupEditMode::ShouldDrawWidget() const
{
	// 这是手柄画不画出来的总开关：GetShowWidget() 遍历所有 EdMode
	// 取这个函数的或（EditorModeManager.cpp:1891-1898）。
	// 没有选中帧时收起手柄，避免在一个没有作用对象的控件上瞎拖。
	return CanManipulate();
}

bool FPoseGroupEditMode::UsesTransformWidget() const
{
	return true;
}

bool FPoseGroupEditMode::UsesTransformWidget(UE::Widget::EWidgetMode CheckMode) const
{
	if (!CanManipulate())
	{
		return false;
	}

	// 只开放平移与旋转。缩放对姿态没有语义：
	// 求解器按位置与偏航插值，缩放既不参与也不该被随手改。
	return CheckMode == UE::Widget::WM_Translate || CheckMode == UE::Widget::WM_Rotate;
}

bool FPoseGroupEditMode::AllowsViewportDragTool() const
{
	// 拖拽由手柄接管，不需要框选之类的视口拖拽工具。
	return false;
}

bool FPoseGroupEditMode::RequiresLegacyViewportInteractions() const
{
	// 这条必须覆写，否则会踩 FEdMode 的默认值 true
	// （LegacyEdModeWidgetHelpers.cpp:560），引擎随即把整个视口切回 legacy 交互，
	// 并在日志里打出「ITF Gizmos and Controls have been disabled」。
	// 一旦切走 ITF，StartTracking 就不再由这条路径调用，
	// InputDelta 永远看到 Manipulating=false 并返回 false，
	// 引擎于是回落成移动相机——也就是「拖手柄结果相机在动」。
	//
	// 引擎里所有资产编辑器的编辑模式都是 false：
	// AnimationEditMode.h:57、AnimNodeEditMode.h:77、
	// StaticMeshEditorEdMode.h:16、PhysicsAssetEditorEditMode.h:55、
	// SequencerEdMode.h:94、DefaultEdMode.cpp:26/61。
	// 只有关卡形态的 LandscapeEdMode.h:478 是 true。
	return false;
}

bool FPoseGroupEditMode::AllowWidgetMove()
{
	return ShouldDrawWidget();
}

FVector FPoseGroupEditMode::GetWidgetLocation() const
{
	// 手柄跟着被拖的组件走，而不是资产里的 key。
	// 拖动过程中引擎改的是组件，资产要等写回才有新值；
	// 读资产会让手柄落后一帧、拖到一半突然跳回去。
	if (PreviewComponent != nullptr && PreviewComponent->IsRegistered())
	{
		return PreviewComponent->GetComponentLocation();
	}

	if (CanManipulate())
	{
		return GroupAsset->Keys[SelectedKeyIndex].LocalLocation;
	}

	return FVector::ZeroVector;
}

bool FPoseGroupEditMode::GetCustomDrawingCoordinateSystem(FMatrix& InMatrix, void* InData)
{
	// 手柄的轴朝向。局部坐标系时跟随角色偏航，
	// 让「拖角色朝前的方向」真的是朝前。
	if (!CanManipulate())
	{
		return false;
	}

	const FRotator YawOnly(0.0, GroupAsset->Keys[SelectedKeyIndex].BodyRotation.Yaw, 0.0);
	InMatrix = FRotationMatrix(YawOnly);
	InMatrix.SetOrigin(FVector::ZeroVector);
	return true;
}

bool FPoseGroupEditMode::GetCustomInputCoordinateSystem(FMatrix& InMatrix, void* InData)
{
	// 输入坐标系必须与绘制坐标系一致，否则拖拽方向会和看到的轴错开。
	return GetCustomDrawingCoordinateSystem(InMatrix, InData);
}

bool FPoseGroupEditMode::StartTracking(FEditorViewportClient* InViewportClient, FViewport* InViewport)
{
	// 这里不要 DisableDeltaModification。
	//
	// 那个开关会把资产上的 Modify() 挡在事务缓冲之外，
	// 结果是整段拖动的撤销栈为空——按 Ctrl+Z 什么都不发生。
	// 撤销点由 ApplyDraggedKey / FinishDrag 里的事务负责。
	bManipulating = true;
	return true;
}

bool FPoseGroupEditMode::EndTracking(FEditorViewportClient* InViewportClient, FViewport* InViewport)
{


	const bool bWasManipulating = bManipulating;
	bManipulating = false;

	// 松手才求解整条链：拖动过程中每帧重算会让手感发涩，
	// 而且中间结果没人看（时间轴与轨迹都没在跟）。
	if (bWasManipulating)
	{
		OnDragFinished.ExecuteIfBound();
	}

	return true;
}

bool FPoseGroupEditMode::InputDelta(FEditorViewportClient* InViewportClient, FViewport* InViewport,
	FVector& InDrag, FRotator& InRot, FVector& InScale)
{
	if (!bManipulating || !CanManipulate())
	{
		return false;
	}

	const EAxisList::Type CurrentAxis = InViewportClient->GetCurrentWidgetAxis();
	if (CurrentAxis == EAxisList::None)
	{
		return false;
	}

	if (PreviewComponent == nullptr)
	{
		return false;
	}

	// 把增量交给引擎应用，不自己算：
	// ApplyDeltaToComponent 负责坐标系换算、绕轴旋转时修正位置、
	// 标脏、PostEditComponentMove、刷新细节面板。这些副作用缺一个都会让
	// 操作手感发涩（比如拖完面板数字不更新）。
	GEditor->ApplyDeltaToComponent(
		PreviewComponent, /*bDelta*/true, &InDrag, &InRot, &InScale,
		PreviewComponent->GetComponentLocation());

	// 组件已被引擎改过，从这里读回它。
	const FVector NewLocation = PreviewComponent->GetRelativeLocation();
	FRotator NewRotation = PreviewComponent->GetRelativeRotation();

	// 旋转只保留偏航。这个系统的朝向语义就是 Yaw，Pitch/Roll 不参与求解；
	// 把完整欧拉角写回去会顺带改掉侧倾与俯仰，表现为拖一下角色就躺下。
	NewRotation.Pitch = 0.0;
	NewRotation.Roll = 0.0;

	OnKeyDragged.ExecuteIfBound(NewLocation, NewRotation);
	return true;
}
