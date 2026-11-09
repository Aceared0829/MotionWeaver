// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Viewport/PoseGroupViewportClient.h"

#include "Pose/PoseGroupProxyAsset.h"
#include "Pose/PoseProxyAsset.h"
#include "Pose/Proxy/PoseChainDebugComponent.h"

#include "EditorViewportClient.h"
#include "SEditorViewport.h"
#include "SceneManagement.h"
#include "SceneView.h"

namespace
{
	const FLinearColor TrajectoryColor(0.16f, 0.63f, 0.86f);
	const FLinearColor ArrowColor(0.47f, 0.78f, 1.0f);
	const FLinearColor GhostKeyColor(1.0f, 0.67f, 0.18f);

	constexpr float TrajectoryThickness = 2.0f;
	constexpr float GhostThickness = 1.0f;
	constexpr float ArrowLength = 20.0f;

	/** Shift 拖拽时每像素转多少度。1 像素 0.5 度：横跨 720 像素刚好转一圈。 */
	constexpr double YawDegreesPerPixel = 0.5;
}

FPoseGroupViewportClient::FPoseGroupViewportClient(
	FPreviewScene* InPreviewScene, const TSharedRef<SEditorViewport>& InViewport)
	: FEditorViewportClient(nullptr, InPreviewScene, InViewport)
{
	SetRealtime(true);

	// 整段运动比单帧大得多，初始视角退远并侧一些，保证能看全走向。
	SetViewLocation(FVector(-420.0, -320.0, 240.0));
	SetViewRotation(FRotator(-18.0, 30.0, 0.0));

	DrawHelper.bDrawGrid = true;
	EngineShowFlags.SetGrid(true);
}

void FPoseGroupViewportClient::SetGroupAsset(UPoseGroupProxyAsset* InAsset)
{
	GroupAsset = InAsset;
	Invalidate();
}

void FPoseGroupViewportClient::SetSelectedKey(int32 InKeyIndex)
{
	SelectedKeyIndex = InKeyIndex;
	Invalidate();
}

const FPoseKey* FPoseGroupViewportClient::GetSelectedKeyPtr() const
{
	const UPoseGroupProxyAsset* Asset = GroupAsset.Get();
	if (Asset == nullptr || !Asset->Keys.IsValidIndex(SelectedKeyIndex))
	{
		return nullptr;
	}
	return &Asset->Keys[SelectedKeyIndex];
}

void FPoseGroupViewportClient::Draw(const FSceneView* View, FPrimitiveDrawInterface* PDI)
{
	FEditorViewportClient::Draw(View, PDI);

	if (PDI == nullptr || !GroupAsset.IsValid())
	{
		return;
	}

	DrawTrajectory(PDI);
	DrawGhostKeys(PDI);
}

void FPoseGroupViewportClient::DrawTrajectory(FPrimitiveDrawInterface* PDI) const
{
	const UPoseGroupProxyAsset* Asset = GroupAsset.Get();
	if (Asset == nullptr)
	{
		return;
	}

	const TArray<FChainTrajectorySample>& Samples = Asset->GetSamples();

	// 采样点逐段连成折线。不再插样条：采样本身就是按帧算的，
	// 再平滑一次会让作者看到的曲线和下游拿到的数据不一致。
	for (int32 Index = 1; Index < Samples.Num(); ++Index)
	{
		PDI->DrawLine(
			Samples[Index - 1].LocalLocation,
			Samples[Index].LocalLocation,
			TrajectoryColor, SDPG_Foreground, TrajectoryThickness);
	}

	for (const FPoseKey& Key : Asset->Keys)
	{
		const double YawRadians = FMath::DegreesToRadians(Key.BodyRotation.Yaw);
		const FVector Forward(FMath::Cos(YawRadians), FMath::Sin(YawRadians), 0.0);
		PDI->DrawLine(
			Key.LocalLocation,
			Key.LocalLocation + Forward * ArrowLength,
			ArrowColor, SDPG_Foreground, 2.0f);
	}

	// 选中帧额外画一个圈：告诉作者「拖它就行」。
	if (const FPoseKey* Selected = GetSelectedKeyPtr())
	{
		DrawWireSphere(PDI, Selected->LocalLocation, FLinearColor(0.31f, 1.0f, 0.55f),
			40.0f, 16, SDPG_Foreground, 1.5f);
	}
}

void FPoseGroupViewportClient::DrawGhostKeys(FPrimitiveDrawInterface* PDI) const
{
	const UPoseGroupProxyAsset* Asset = GroupAsset.Get();
	if (Asset == nullptr)
	{
		return;
	}

	for (int32 Index = 0; Index < Asset->Keys.Num(); ++Index)
	{
		const FPoseKey& Key = Asset->Keys[Index];

		// 当前帧由真蒙皮显示，不画骨架，否则线会穿在角色身上。
		if (Index == SelectedKeyIndex)
		{
			continue;
		}

		const UPoseChainDebugComponent::FStickFigure Figure =
			UPoseChainDebugComponent::BuildStickFigure(Key.PoseAsset, Key.LocalLocation);

		if (Figure.BonePoints.Num() == 0)
		{
			PDI->DrawPoint(Key.LocalLocation, GhostKeyColor, 10.0f, SDPG_Foreground);
			continue;
		}

		for (int32 BoneIndex = 0; BoneIndex < Figure.BonePoints.Num(); ++BoneIndex)
		{
			const int32 ParentIndex = Figure.ParentIndices.IsValidIndex(BoneIndex)
				? Figure.ParentIndices[BoneIndex]
				: INDEX_NONE;
			if (ParentIndex == INDEX_NONE || !Figure.BonePoints.IsValidIndex(ParentIndex))
			{
				continue;
			}

			PDI->DrawLine(
				Figure.Origin + Figure.BonePoints[ParentIndex],
				Figure.Origin + Figure.BonePoints[BoneIndex],
				GhostKeyColor, SDPG_Foreground, GhostThickness);
		}
	}
}

bool FPoseGroupViewportClient::ScreenToGround(
	const FViewport* InViewport, int32 ScreenX, int32 ScreenY, FVector& OutWorld)
{
	if (InViewport == nullptr)
	{
		return false;
	}

	const FSceneView* View = nullptr;
	// 用当前视图的矩阵把屏幕点反投影成射线。GetViewRightVector 之类
	// 在编辑器视口里有现成封装，但反投影要用完整矩阵，直接调静态函数更稳。
	const FIntPoint ViewportSize = InViewport->GetSizeXY();
	if (ViewportSize.X <= 0 || ViewportSize.Y <= 0)
	{
		return false;
	}

	// DeprojectScreenToWorld 需要视图与投影矩阵；这里通过 ViewFamily 现取一份。
	FSceneViewFamilyContext ViewFamily(FSceneViewFamily::ConstructionValues(
		const_cast<FViewport*>(InViewport), GetScene(), EngineShowFlags));
	View = CalcSceneView(&ViewFamily);
	if (View == nullptr)
	{
		return false;
	}

	FVector WorldOrigin;
	FVector WorldDirection;
	FSceneView::DeprojectScreenToWorld(
		FVector2D(ScreenX, ScreenY),
		FIntRect(0, 0, ViewportSize.X, ViewportSize.Y),
		View->ViewMatrices.GetInvViewProjectionMatrix(),
		WorldOrigin, WorldDirection);

	// 与 Z=0 平面求交。方向几乎平行于地面时不拖（否则点会飞到极远处）。
	if (FMath::Abs(WorldDirection.Z) < 1e-4)
	{
		return false;
	}

	const double T = -WorldOrigin.Z / WorldDirection.Z;
	if (T <= 0.0)
	{
		return false;
	}

	OutWorld = WorldOrigin + WorldDirection * T;
	OutWorld.Z = 0.0;
	return true;
}

bool FPoseGroupViewportClient::InputKey(const FInputKeyEventArgs& EventArgs)
{
	// FInputKeyEventArgs 不带鼠标坐标（只有键、事件、设备），
	// 所以按下时无法立刻求地面点。真正的起点在第一次 CapturedMouseMove 里补。
	if (EventArgs.Key == EKeys::LeftMouseButton && EventArgs.Event == IE_Pressed)
	{
		const FPoseKey* Selected = GetSelectedKeyPtr();
		if (Selected != nullptr && EventArgs.Viewport != nullptr)
		{
			bDragging = true;
			bHasDragStart = false;
			bDragRotateMode = IsShiftPressed();
			DragStartKey = *Selected;
			LastMousePos = FIntPoint::ZeroValue;

			OnDragStarted.ExecuteIfBound();
			return true;
		}
	}
	else if (EventArgs.Key == EKeys::LeftMouseButton && EventArgs.Event == IE_Released)
	{
		if (bDragging)
		{
			bDragging = false;
			bHasDragStart = false;
			OnDragFinished.ExecuteIfBound();
			return true;
		}
	}

	return FEditorViewportClient::InputKey(EventArgs);
}

void FPoseGroupViewportClient::CapturedMouseMove(FViewport* InViewport, int32 InMouseX, int32 InMouseY)
{
	if (!bDragging)
	{
		FEditorViewportClient::CapturedMouseMove(InViewport, InMouseX, InMouseY);
		return;
	}

	ApplyDrag(InMouseX, InMouseY);
}

void FPoseGroupViewportClient::ApplyDrag(int32 MouseX, int32 MouseY)
{
	const FPoseKey* Selected = GetSelectedKeyPtr();
	if (Selected == nullptr || Viewport == nullptr)
	{
		return;
	}

	// 第一次移动才拿得到坐标：用它同时定下「按下的地面点」和「按下的像素点」。
	if (!bHasDragStart)
	{
		if (!bDragRotateMode && !ScreenToGround(Viewport, MouseX, MouseY, DragStartWorld))
		{
			return;
		}

		DragStartMouse = FIntPoint(MouseX, MouseY);
		LastMousePos = DragStartMouse;
		bHasDragStart = true;
		return;
	}

	LastMousePos = FIntPoint(MouseX, MouseY);

	// 每次都以「按下时的原始值 + 总位移」计算，而不是逐帧累加增量：
	// 累加会把舍入误差攒起来，松手后位置会跟手追过的轨迹对不上。
	FPoseKey NewKey = DragStartKey;

	if (bDragRotateMode)
	{
		// Shift 模式：横向像素直接映射成偏航角。
		const double DeltaYaw =
			static_cast<double>(MouseX - DragStartMouse.X) * YawDegreesPerPixel;
		NewKey.BodyRotation = FRotator(
			DragStartKey.BodyRotation.Pitch,
			DragStartKey.BodyRotation.Yaw + DeltaYaw,
			DragStartKey.BodyRotation.Roll);
		NewKey.HeadingDegrees = NewKey.BodyRotation.Yaw;
	}
	else
	{
		// 普通模式：把鼠标当前点投到地面，与按下点的差就是平移量。
		FVector CurrentGround;
		if (!ScreenToGround(Viewport, MouseX, MouseY, CurrentGround))
		{
			return;
		}

		NewKey.LocalLocation = DragStartKey.LocalLocation + (CurrentGround - DragStartWorld);
		NewKey.LocalLocation.Z = DragStartKey.LocalLocation.Z;
	}

	OnKeyDragged.ExecuteIfBound(NewKey);
}
