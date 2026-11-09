// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Viewport/PoseGroupViewportClient.h"

#include "Pose/PoseGroupProxyAsset.h"
#include "Pose/PoseProxyAsset.h"
#include "Pose/Proxy/PoseChainDebugComponent.h"

#include "EditorViewportClient.h"
#include "SEditorViewport.h"
#include "SceneManagement.h"

namespace
{
	const FLinearColor TrajectoryColor(0.16f, 0.63f, 0.86f);
	const FLinearColor ArrowColor(0.47f, 0.78f, 1.0f);
	const FLinearColor GhostKeyColor(1.0f, 0.67f, 0.18f);

	constexpr float TrajectoryThickness = 2.0f;
	constexpr float GhostThickness = 1.0f;
	constexpr float ArrowLength = 20.0f;
}

FPoseGroupViewportClient::FPoseGroupViewportClient(
	FPreviewScene* InPreviewScene, const TSharedRef<SEditorViewport>& InViewport)
	: FEditorViewportClient(nullptr, InPreviewScene, InViewport)
{
	SetRealtime(true);

	// 整段运动比单帧大得多，初始视角退远一点并侧一些，保证能看全走向。
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

	// 每个关键帧画一支朝向箭头：一眼看出这一帧面朝哪。
	for (const FPoseKey& Key : Asset->Keys)
	{
		const double YawRadians = FMath::DegreesToRadians(Key.BodyRotation.Yaw);
		const FVector Forward(FMath::Cos(YawRadians), FMath::Sin(YawRadians), 0.0);
		PDI->DrawLine(
			Key.LocalLocation,
			Key.LocalLocation + Forward * ArrowLength,
			ArrowColor, SDPG_Foreground, 2.0f);
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

		// 画不出骨架（资产为空）就只在落点放一个点，
		// 至少让作者知道这里有一个关键帧。
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
