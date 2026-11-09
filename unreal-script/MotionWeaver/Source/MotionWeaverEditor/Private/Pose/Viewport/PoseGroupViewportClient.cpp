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
	// 构造函数里只做纯数据初始化。
	//
	// 不能在这里调 SetViewLocation / SetViewRotation / SetWidgetMode：
	//  - 前两个会走 Slate 的 FSlateApplication，构造期 GUI 还没起来
	//    （无头运行更是完全不存在），会命中
	//    Assertion failed: CurrentApplication.IsValid()；
	//  - SetWidgetMode 内部访问 ModeTools，而 ModeTools 由视口在构造后注入。
	// 三者统一挪到第一次 Tick，那里上述依赖都已就绪。
	SetRealtime(true);

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

void FPoseGroupViewportClient::Tick(float DeltaSeconds)
{
	FEditorViewportClient::Tick(DeltaSeconds);

	// 首次 Tick 时才做视口初始化：此时 Slate 应用与 ModeTools 都已就绪，
	// 构造期做这些会崩（见构造函数里的说明）。
	if (!bViewportInitialized)
	{
		bViewportInitialized = true;

		// 整段运动比单帧大得多，初始视角退远并侧一些，保证能看全走向。
		SetViewLocation(FVector(-420.0, -320.0, 240.0));
		SetViewRotation(FRotator(-18.0, 30.0, 0.0));

		if (ModeTools != nullptr)
		{
			SetWidgetMode(UE::Widget::WM_Translate);
		}
	}
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

FMatrix FPoseGroupViewportClient::GetWidgetCoordSystem() const
{
	// 局部坐标系：Widget 的轴跟着角色朝向转。作者拖「角色朝前的方向」时，
	// 拖的就真的是朝前，而不是世界 +X。
	// 世界坐标系直接返回单位矩阵，引擎就按世界轴摆。
	if (WidgetCoordSystem == COORD_Local)
	{
		if (const FPoseKey* Selected = GetSelectedKeyPtr())
		{
			return FRotationMatrix(FRotator(0.0, Selected->BodyRotation.Yaw, 0.0));
		}
	}

	return FMatrix::Identity;
}

ECoordSystem FPoseGroupViewportClient::GetWidgetCoordSystemSpace() const
{
	return WidgetCoordSystem;
}

void FPoseGroupViewportClient::SetWidgetCoordSystemSpace(ECoordSystem NewCoordSystem)
{
	WidgetCoordSystem = NewCoordSystem;
	Invalidate();
}

bool FPoseGroupViewportClient::InputWidgetDelta(
	FViewport* InViewport, EAxisList::Type CurrentAxis,
	FVector& Drag, FRotator& Rot, FVector& Scale)
{
	const FPoseKey* Selected = GetSelectedKeyPtr();
	if (Selected == nullptr || DragTarget == nullptr)
	{
		return false;
	}

	// 交给引擎处理，不自己算：
	// UEditorEngine::ApplyDeltaToComponent 负责坐标系换算、绕轴旋转时
	// 修正位置、标脏、PostEditComponentMove、刷新细节面板——
	// 这些副作用缺一个都会让操作手感发涩（比如拖完面板数字不更新）。
	//
	// 注意 Drag/Rot 是「这一帧的增量」而不是累计总量：
	// FMouseDeltaTracker 每帧只把本帧鼠标位移转成轴位移，
	// 因此必须以 bDelta=true 的语义增量应用。
	GEditor->ApplyDeltaToComponent(
		DragTarget, /*bDelta*/true, &Drag, &Rot, &Scale,
		DragTarget->GetComponentLocation());

	// 组件已被引擎改过，从这里读回它，同步到 key。
	FPoseKey NewKey = *Selected;
	NewKey.LocalLocation = DragTarget->GetRelativeLocation();

	// 旋转只保留偏航：这个系统的朝向语义就是 Yaw。
	// Pitch/Roll 能在 BodyRotation 里存，但旋转手柄的圆弧拖动在这里
	// 容易误操作，等有明确需求再开放。
	const FRotator ComponentRotation = DragTarget->GetRelativeRotation();
	NewKey.BodyRotation = FRotator(
		ComponentRotation.Pitch, ComponentRotation.Yaw, ComponentRotation.Roll);
	NewKey.HeadingDegrees = NewKey.BodyRotation.Yaw;

	OnKeyDragged.ExecuteIfBound(NewKey);
	return true;
}

bool FPoseGroupViewportClient::InputKey(const FInputKeyEventArgs& EventArgs)
{
	// 与关卡编辑器一致的快捷键：W 平移 / E 旋转 / X 切换世界-局部坐标系。
	// 这些键是用户肌肉记忆的一部分，换成别的会立刻觉得「别扭」。
	if (EventArgs.Event == IE_Pressed && ModeTools != nullptr)
	{
		if (EventArgs.Key == EKeys::W)
		{
			SetWidgetMode(UE::Widget::WM_Translate);
			return true;
		}
		if (EventArgs.Key == EKeys::E)
		{
			SetWidgetMode(UE::Widget::WM_Rotate);
			return true;
		}
		if (EventArgs.Key == EKeys::X)
		{
			SetWidgetCoordSystemSpace(
				WidgetCoordSystem == COORD_World ? COORD_Local : COORD_World);
			return true;
		}
	}

	return FEditorViewportClient::InputKey(EventArgs);
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
