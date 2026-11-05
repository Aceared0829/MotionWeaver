// Copyright ZhaoZining. All Rights Reserved.

#include "MotionWeaverPoseViewportClient.h"

#include "AdvancedPreviewScene.h"
#include "SEditorViewport.h"

FMotionWeaverPoseViewportClient::FMotionWeaverPoseViewportClient(
	FPreviewScene* InPreviewScene, const TSharedRef<SEditorViewport>& InViewport)
	: FEditorViewportClient(nullptr, InPreviewScene, InViewport)
{
	// 姿态是静态的，不需要在实时渲染之外推进任何时钟。
	SetRealtime(true);

	// 初始视角对准角色正面，离得比默认近一点，方便看姿态细节。
	SetViewLocation(FVector(-220.0, 0.0, 90.0));
	SetViewRotation(FRotator::ZeroRotator);

	DrawHelper.bDrawGrid = false;
	EngineShowFlags.SetGrid(false);
}
