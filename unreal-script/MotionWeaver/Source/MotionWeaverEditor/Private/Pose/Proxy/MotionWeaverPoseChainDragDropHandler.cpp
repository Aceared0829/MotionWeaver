// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Proxy/MotionWeaverPoseChainDragDropHandler.h"

#include "Pose/MotionWeaverPoseAsset.h"
#include "Pose/Proxy/MotionWeaverPoseChainActor.h"

#include "Editor.h"
#include "Engine/World.h"
#include "LevelEditorViewport.h"
#include "SceneView.h"
#include "ScopedTransaction.h"
#include "Selection.h"
#include "UnrealClient.h"

#define LOCTEXT_NAMESPACE "MotionWeaverPoseChainDragDrop"

namespace
{
	/** 当前选中的链 Actor；必须恰好一个，否则返回 nullptr。 */
	AMotionWeaverPoseChainActor* GetSingleSelectedChainActor()
	{
		if (GEditor == nullptr)
		{
			return nullptr;
		}

		AMotionWeaverPoseChainActor* Found = nullptr;
		for (FSelectionIterator It(GEditor->GetSelectedActorIterator()); It; ++It)
		{
			if (AMotionWeaverPoseChainActor* ChainActor = Cast<AMotionWeaverPoseChainActor>(*It))
			{
				if (Found != nullptr)
				{
					// 选中了多个链时不做决定：交给默认行为，避免猜错对象。
					return nullptr;
				}
				Found = ChainActor;
			}
		}
		return Found;
	}

	/** 把鼠标位置换成世界落点；打不到任何东西时沿视线取一个固定距离处的点。 */
	FVector ResolveCursorWorldLocation(FViewport* Viewport, int32 MouseX, int32 MouseY)
	{
		if (GEditor == nullptr || Viewport == nullptr)
		{
			return FVector::ZeroVector;
		}

		for (FLevelEditorViewportClient* ViewportClient : GEditor->GetLevelViewportClients())
		{
			if (ViewportClient == nullptr || ViewportClient->Viewport != Viewport)
			{
				continue;
			}

			FSceneViewFamilyContext ViewFamily(FSceneViewFamily::ConstructionValues(
				Viewport, ViewportClient->GetScene(), ViewportClient->EngineShowFlags)
				.SetRealtimeUpdate(ViewportClient->IsRealtime()));
			const FSceneView* View = ViewportClient->CalcSceneView(&ViewFamily);
			if (View == nullptr)
			{
				break;
			}

			const FViewportCursorLocation Cursor(View, ViewportClient, MouseX, MouseY);
			const FVector Origin = Cursor.GetOrigin();
			const FVector Direction = Cursor.GetDirection();

			UWorld* World = ViewportClient->GetWorld();
			if (World != nullptr)
			{
				FHitResult Hit;
				FCollisionQueryParams Params(SCENE_QUERY_STAT(MotionWeaverPoseChainDrop), false);
				// 命中场景里的任何东西都行：地面、箱子、角色都可以当落点。
				if (World->LineTraceSingleByChannel(Hit, Origin, Origin + Direction * 100000.0,
					ECC_Visibility, Params))
				{
					return Hit.ImpactPoint;
				}
			}

			// 什么都没打到（对着天空拖）时取视线前方 5 米，至少不会全叠在原点。
			return Origin + Direction * 500.0;
		}

		return FVector::ZeroVector;
	}
}

bool UMotionWeaverPoseChainDragDropHandler::PreDropObjectsAtCoordinates(
	int32 MouseX, int32 MouseY, UWorld* World, FViewport* Viewport,
	const TArray<UObject*>& DroppedObjects, TArray<AActor*>& OutNewActors)
{
	// 只处理姿势资产；其它资产落到关卡里还是按引擎默认行为处理。
	TArray<UMotionWeaverPoseAsset*> PoseAssets;
	PoseAssets.Reserve(DroppedObjects.Num());
	for (UObject* Dropped : DroppedObjects)
	{
		if (UMotionWeaverPoseAsset* PoseAsset = Cast<UMotionWeaverPoseAsset>(Dropped))
		{
			PoseAssets.Add(PoseAsset);
		}
	}

	if (PoseAssets.Num() == 0)
	{
		return true;
	}

	AMotionWeaverPoseChainActor* ChainActor = GetSingleSelectedChainActor();
	if (ChainActor == nullptr)
	{
		// 没选中链（或选了好几个）：不插手，让默认工厂去建单帧 Actor。
		return true;
	}

	const FVector WorldLocation = ResolveCursorWorldLocation(Viewport, MouseX, MouseY);
	const FVector LocalLocation = ChainActor->GetActorTransform().InverseTransformPosition(WorldLocation);
	const double Heading = ChainActor->GetActorRotation().Yaw;

	// 一条事务覆盖整次拖放：撤销一次回到拖入之前。
	const FScopedTransaction Transaction(LOCTEXT("DropPoseIntoChain", "往姿态链添加姿势"));
	ChainActor->Modify();

	// 多个资产同时拖入时沿线往后排，避免全叠在同一个点上。
	const double SpacingPerDrop = 120.0;
	for (int32 Index = 0; Index < PoseAssets.Num(); ++Index)
	{
		const FVector Offset(SpacingPerDrop * Index, 0.0, 0.0);
		ChainActor->AppendKey(PoseAssets[Index], LocalLocation + Offset, Heading);
	}

	// 返回 false 取消默认拖放：否则引擎还会再建一个单帧 Actor 出来。
	return false;
}

#undef LOCTEXT_NAMESPACE
