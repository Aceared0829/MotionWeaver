// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Viewport/PoseGroupViewportClient.h"

#include "Pose/PoseGroupProxyAsset.h"
#include "Pose/PoseProxyAsset.h"
#include "Pose/Proxy/PoseChainDebugComponent.h"

#include "EditorViewportClient.h"
#include "EditorModeManager.h"
#include "SEditorViewport.h"
#include "SceneManagement.h"
#include "UnrealWidget.h"

#include "LevelEditorViewport.h"
#include "Settings/LevelEditorViewportSettings.h"
#include "ViewportClientNavigationHelper.h"

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
	// 构造里只碰「不依赖 Slate 应用、不依赖 ModeTools」的东西。
	// 参照 FSCSEditorViewportClient 的构造函数：它在这里设的是
	// WidgetMode / WidgetCoordSystem 这两个纯成员变量，而不是调 SetWidgetMode。
	WidgetMode = UE::Widget::WM_Translate;
	WidgetCoordSystem = COORD_Local;

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

	// 首次 Tick 才做需要 Slate / ModeTools 的初始化。
	// 构造期做这些会崩：SetViewLocation 走 FSlateApplication，
	// 而 ModeTools 在父类构造函数靠后位置才创建（EditorViewportClient.cpp:590）。
	if (!bViewportInitialized)
	{
		bViewportInitialized = true;

		// 整段运动比单帧大得多，初始视角退远并侧一些，保证能看全走向。
		SetViewLocation(FVector(-420.0, -320.0, 240.0));
		SetViewRotation(FRotator(-18.0, 30.0, 0.0));

		// 吸附：这是拖动时「咬住网格」的来源，没有它手感会飘。
		// 蓝图 SCS 视口在构造里就 Widget->SetSnapEnabled(true)，
		// 这里等 Widget 就绪后补上。
		if (Widget != nullptr)
		{
			Widget->SetSnapEnabled(true);
		}

		// 不把模式转给 ModeTools：WidgetMode 是本类自己维护的成员
		// （见 SetWidgetMode / GetWidgetMode），转手反而会与它失配。

		// 选中轮廓：让「当前在动哪一帧」有视觉反馈。
		// 蓝图 SCS 视口读的是 ULevelEditorViewportSettings::bUseSelectionOutline，
		// 这里跟随同一项设置，用户在关卡里关掉轮廓时这里也一致。
		EngineShowFlags.SetSelectionOutline(
			GetDefault<ULevelEditorViewportSettings>()->bUseSelectionOutline);

		// 自检日志：只打一次，用来分清「没生效」和「生效了但表现不同」。
		if (!bLoggedInit)
		{
			bLoggedInit = true;
			UE_LOG(LogTemp, Warning,
				TEXT("[PoseGroupViewport] 初始化：Widget=%s ModeTools=%s Snap=%d Mode=%d Coord=%d DragTarget=%s"),
				Widget != nullptr ? TEXT("有") : TEXT("空"),
				ModeTools != nullptr ? TEXT("有") : TEXT("空"),
				Widget != nullptr ? 1 : 0,
				static_cast<int32>(WidgetMode),
				static_cast<int32>(WidgetCoordSystem),
				DragTarget != nullptr ? TEXT("有") : TEXT("无"));
		}
	}

	// 预览世界的 Tick：引擎的视口客户端每帧推进一次自己的预览场景，
	// 少了这一步组件注册、材质解析、渲染状态更新都会滞后，
	// 表现为「拖完要动一下鼠标才刷新」。
	if (PreviewScene != nullptr)
	{
		if (UWorld* PreviewWorld = PreviewScene->GetWorld())
		{
			PreviewWorld->Tick(IsRealtime() ? LEVELTICK_ViewportsOnly : LEVELTICK_TimeOnly, DeltaSeconds);
		}
	}
}

FVector FPoseGroupViewportClient::GetWidgetLocation() const
{
	// 手柄位置优先读被拖的组件，而不是资产里的 key。
	//
	// 拖动过程中引擎直接改组件，资产要等编辑器写回才有新值：
	// 如果这里读资产，手柄会落后一帧甚至停在原地，表现为「手柄不跟手」
	// 或者拖到一半突然跳回。
	if (DragTarget != nullptr && DragTarget->IsRegistered())
	{
		return DragTarget->GetComponentLocation();
	}

	if (const FPoseKey* Selected = GetSelectedKeyPtr())
	{
		return Selected->LocalLocation;
	}

	// 没有选中帧时把手柄挪到极远处，避免它停在原处让人误以为还能操作。
	return FVector(0.0, 0.0, -100000.0);
}

void FPoseGroupViewportClient::SetWidgetMode(UE::Widget::EWidgetMode NewMode)
{
	WidgetMode = NewMode;

	// 自己存一份给鼠标位移换算使用，同时让引擎也知道模式。
	// 只写字段会出现「界面看似切换了，实际拖拽仍按旧模式走」。
	if (ModeTools != nullptr)
	{
		ModeTools->SetWidgetMode(NewMode);
	}

	Invalidate();
}

UE::Widget::EWidgetMode FPoseGroupViewportClient::GetWidgetMode() const
{
	return WidgetMode;
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

	if (ModeTools != nullptr)
	{
		ModeTools->SetCoordSystem(NewCoordSystem);
	}

	Invalidate();
}

bool FPoseGroupViewportClient::InputWidgetDelta(
	FViewport* InViewport, EAxisList::Type CurrentAxis,
	FVector& Drag, FRotator& Rot, FVector& Scale)
{
	const FPoseKey* Selected = GetSelectedKeyPtr();

	// 蓝图视口的门控：只有手柄真正被拖动、且有明确轴时才改对象。
	// 缺了这个条件，普通视口鼠标操作也可能把 key 改掉。
	if (Selected == nullptr || DragTarget == nullptr
		|| !bIsManipulating || CurrentAxis == EAxisList::None)
	{
		return FEditorViewportClient::InputWidgetDelta(InViewport, CurrentAxis, Drag, Rot, Scale);
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

	if (!Rot.IsNearlyZero())
	{
		// 旋转只改偏航，保留这一帧原有的 Pitch/Roll。
		//
		// ApplyDeltaToComponent 接收的是欧拉增量并做四元数组合，
		// 直接把组件的完整旋转写回会顺带改变侧倾和俯仰，
		// 表现就是「拖一下旋转，角色侧翻躺地上」。
		NewKey.BodyRotation = FRotator(
			Selected->BodyRotation.Pitch,
			Selected->BodyRotation.Yaw + Rot.Yaw,
			Selected->BodyRotation.Roll);
		NewKey.HeadingDegrees = NewKey.BodyRotation.Yaw;
	}

	if (!bLoggedDrag)
	{
		bLoggedDrag = true;
		UE_LOG(LogTemp, Warning,
			TEXT("[PoseGroupViewport] 拖拽：轴=%d 平移=(%.2f,%.2f,%.2f) Yaw增量=%.2f 新Yaw=%.2f"),
			static_cast<int32>(CurrentAxis), Drag.X, Drag.Y, Drag.Z, Rot.Yaw, NewKey.BodyRotation.Yaw);
	}

	OnKeyDragged.ExecuteIfBound(NewKey);
	return true;
}

bool FPoseGroupViewportClient::InputKey(const FInputKeyEventArgs& EventArgs)
{
	// 相机导航键一个都不能拦。
	//
	// 引擎把整套字母键都给了视口导航（EditorViewportClient.cpp:7570-7581）：
	//   W/S 前后、A/D 左右、E/Q 世界上/下、R/F 局部上/下、C/Z 视野缩放。
	// 之前用 W/E 切平移与旋转模式，等于按住右键飞相机时把前进和上移吃掉，
	// 相机停在原地、右键却仍处于 mouse-look 状态——表现就是视口卡住。
	// 关卡编辑器本身也不用 W/E 切 gizmo，而是工具栏按钮 + 空格循环。
	//
	// 所以这里只保留 X（引擎未占用）作为坐标系切换，其余全部交回父类。
	const bool bCameraNavigating =
		EventArgs.Viewport != nullptr
		&& EventArgs.Viewport->KeyState(EKeys::RightMouseButton);
	const bool bMouseLooking = GetViewportNavigationHelper()->bIsMouseLooking;

	const bool bCanUseShortcuts =
		EventArgs.Event == IE_Pressed
		&& ModeTools != nullptr
		&& !bCameraNavigating
		&& !bMouseLooking
		&& !bIsManipulating;

	// 空格循环模式：与关卡编辑器一致，且空格不参与相机导航。
	if (bCanUseShortcuts && EventArgs.Key == EKeys::SpaceBar)
	{
		CycleWidgetMode();
		return true;
	}

	// X 切换世界/局部坐标系。引擎没有给 X 分配导航语义。
	if (bCanUseShortcuts && EventArgs.Key == EKeys::X)
	{
		SetWidgetCoordSystemSpace(
			WidgetCoordSystem == COORD_World ? COORD_Local : COORD_World);
		return true;
	}

	return FEditorViewportClient::InputKey(EventArgs);
}

void FPoseGroupViewportClient::CycleWidgetMode()
{
	// 平移 → 旋转 → 平移。只在这两个之间循环：
	// 缩放对这个系统没有意义（姿态缩放不参与求解，也不该被随手改）。
	SetWidgetMode(
		WidgetMode == UE::Widget::WM_Translate
			? UE::Widget::WM_Rotate
			: UE::Widget::WM_Translate);
}

void FPoseGroupViewportClient::TrackingStarted(
	const FInputEventState& InInputState, bool bIsDraggingWidget, bool bNudge)
{
	FEditorViewportClient::TrackingStarted(InInputState, bIsDraggingWidget, bNudge);

	if (bIsDraggingWidget)
	{
		// 拖手柄才开事务；普通点击不开，否则每点一下撤销栈就多一层。
		bIsManipulating = true;
		bHasDragStart = true;
		bLoggedDrag = false;
		OnDragStarted.ExecuteIfBound();
	}
}

void FPoseGroupViewportClient::TrackingStopped()
{
	FEditorViewportClient::TrackingStopped();

	if (bIsManipulating)
	{
		bIsManipulating = false;
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

	if (!bLoggedWidget && DragTarget != nullptr)
	{
		bLoggedWidget = true;
		UE_LOG(LogTemp, Warning,
			TEXT("[PoseGroupViewport] 手柄=%s 组件=%s 选中帧=%d 资产Key数=%d"),
			*GetWidgetLocation().ToString(),
			*DragTarget->GetComponentLocation().ToString(),
			SelectedKeyIndex,
			GroupAsset.IsValid() ? GroupAsset->Keys.Num() : -1);
	}
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
