// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Viewport/PoseGroupViewportClient.h"

#include "Pose/PoseGroupProxyAsset.h"
#include "Pose/PoseProxyAsset.h"
#include "Pose/Proxy/PoseChainDebugComponent.h"

#include "EditorViewportClient.h"
#include "SEditorViewport.h"
#include "SceneManagement.h"
#include "UnrealWidget.h"

namespace
{
	const FLinearColor TrajectoryColor(0.16f, 0.63f, 0.86f);
	const FLinearColor ArrowColor(0.47f, 0.78f, 1.0f);
	const FLinearColor GhostKeyColor(1.0f, 0.67f, 0.18f);
	const FLinearColor SelectedRingColor(0.31f, 1.0f, 0.55f);

	constexpr float TrajectoryThickness = 2.0f;
	constexpr float GhostThickness = 1.0f;
	constexpr float ArrowLength = 20.0f;
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

	// 默认平移手柄：摆位置是最高频的操作。
	SetWidgetMode(UE::Widget::WM_Translate);
}

void FPoseGroupViewportClient::SetGroupAsset(UPoseGroupProxyAsset* InAsset)
{
	GroupAsset = InAsset;
	Invalidate();
}

void FPoseGroupViewportClient::SetSelectedKey(int32 InKeyIndex)
{
	SelectedKeyIndex = InKeyIndex;
	bHasDragStart = false;
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

FVector FPoseGroupViewportClient::GetWidgetLocation() const
{
	// 手柄的位置由引擎每帧问这里，不需要自己 SetLocation
	// （FWidget 也没有那个接口）。没有选中帧时把手柄挪到原点之外，
	// 避免它停在上一帧的位置让人误以为还在操作。
	if (const FPoseKey* Selected = GetSelectedKeyPtr())
	{
		return Selected->LocalLocation;
	}

	return FVector(0.0, 0.0, -100000.0);
}

bool FPoseGroupViewportClient::InputWidgetDelta(
	FViewport* InViewport, EAxisList::Type CurrentAxis,
	FVector& Drag, FRotator& Rot, FVector& Scale)
{
	const FPoseKey* Selected = GetSelectedKeyPtr();
	if (Selected == nullptr)
	{
		return false;
	}

	bool bChanged = false;

	// 每次拖动的第一帧拿到「起点值」，之后都以它为基准重算，
	// 而不是在上一帧结果上累加——累加会把舍入误差攒起来，松手后位置会偏。
	if (!bHasDragStart)
	{
		DragStartKey = *Selected;
		bHasDragStart = true;
	}

	FPoseKey NewKey = DragStartKey;

	if (!Drag.IsNearlyZero())
	{
		// 三个轴都放开：LocalLocation 是 FVector，求解器按三维 Hermite 插值。
		NewKey.LocalLocation = DragStartKey.LocalLocation + Drag;
		bChanged = true;
	}

	if (!Rot.IsNearlyZero())
	{
		// 旋转手柄给的是增量旋转；这里只取 Yaw。
		// Pitch/Roll 能存进 BodyRotation，但旋转手柄的圆弧拖动在
		// 这个只有偏航有语义的场景里容易误操作，先不开放。
		NewKey.BodyRotation = FRotator(
			DragStartKey.BodyRotation.Pitch,
			DragStartKey.BodyRotation.Yaw + Rot.Yaw,
			DragStartKey.BodyRotation.Roll);
		NewKey.HeadingDegrees = NewKey.BodyRotation.Yaw;
		bChanged = true;
	}

	if (!bChanged)
	{
		return false;
	}

	OnKeyDragged.ExecuteIfBound(NewKey);
	return true;
}

void FPoseGroupViewportClient::TrackingStarted(
	const FInputEventState& InInputState, bool bIsDraggingWidget, bool bNudge)
{
	FEditorViewportClient::TrackingStarted(InInputState, bIsDraggingWidget, bNudge);

	if (bIsDraggingWidget)
	{
		// 拖手柄才开事务；普通点击不开，否则每点一下撤销栈就多一层。
		bHasDragStart = false;
		OnDragStarted.ExecuteIfBound();
	}
}

void FPoseGroupViewportClient::TrackingStopped()
{
	FEditorViewportClient::TrackingStopped();

	if (bHasDragStart)
	{
		bHasDragStart = false;
		OnDragFinished.ExecuteIfBound();
	}
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

	// 选中帧额外画一个圈：告诉作者手柄作用在这一帧上。
	if (const FPoseKey* Selected = GetSelectedKeyPtr())
	{
		DrawWireSphere(PDI, Selected->LocalLocation, SelectedRingColor,
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
