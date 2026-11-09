// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Viewport/SPoseGroupViewport.h"

#include "Pose/PoseGroupProxyAsset.h"
#include "Pose/PoseProxyAsset.h"
#include "Pose/PoseComponent.h"
#include "Pose/Viewport/PoseGroupViewportClient.h"

#include "AdvancedPreviewScene.h"
#include "SEditorViewport.h"

void SPoseGroupViewport::Construct(const FArguments& InArgs)
{
	GroupAsset = InArgs._GroupAsset;

	// 预览场景自带地板与天光；整段运动会铺开，地板给个参照。
	PreviewScene = MakeShared<FAdvancedPreviewScene>(FPreviewScene::ConstructionValues());

	PreviewComponent = NewObject<UPoseComponent>(GetTransientPackage());
	PreviewComponent->SetFlags(RF_Transient);
	PreviewScene->AddComponent(PreviewComponent, FTransform::Identity);

	SEditorViewport::Construct(SEditorViewport::FArguments());

	SetSelectedKey(GroupAsset != nullptr && GroupAsset->Keys.Num() > 0 ? 0 : INDEX_NONE);
}

void SPoseGroupViewport::SetGroupAsset(UPoseGroupProxyAsset* InAsset)
{
	GroupAsset = InAsset;

	if (FPoseGroupViewportClient* ViewportClientPtr =
		static_cast<FPoseGroupViewportClient*>(GetViewportClient().Get()))
	{
		ViewportClientPtr->SetGroupAsset(InAsset);
	}

	SetSelectedKey(InAsset != nullptr && InAsset->Keys.Num() > 0 ? 0 : INDEX_NONE);
}

void SPoseGroupViewport::SetSelectedKey(int32 KeyIndex)
{
	SelectedKeyIndex = KeyIndex;

	if (FPoseGroupViewportClient* ViewportClientPtr =
		static_cast<FPoseGroupViewportClient*>(GetViewportClient().Get()))
	{
		ViewportClientPtr->SetSelectedKey(KeyIndex);
	}

	ApplySelectedPose();
}

void SPoseGroupViewport::ApplySelectedPose()
{
	if (PreviewComponent == nullptr)
	{
		return;
	}

	// 没有可显示的帧就清掉姿态，别留下上一帧的影子。
	if (GroupAsset == nullptr || !GroupAsset->Keys.IsValidIndex(SelectedKeyIndex))
	{
		PreviewComponent->SetVisibility(false);
		return;
	}

	const FPoseKey& Key = GroupAsset->Keys[SelectedKeyIndex];

	// 位置与朝向由 key 决定；蒙皮网格本身不带根位移。
	PreviewComponent->SetRelativeLocationAndRotation(Key.LocalLocation, Key.BodyRotation);

	const bool bHasMesh = Key.PoseAsset != nullptr && !Key.PoseAsset->PreviewMesh.IsNull();
	PreviewComponent->SetVisibility(bHasMesh);

	if (bHasMesh)
	{
		PreviewComponent->ApplyPoseAsset(Key.PoseAsset);
	}
}

TSharedRef<FEditorViewportClient> SPoseGroupViewport::MakeEditorViewportClient()
{
	TSharedRef<FPoseGroupViewportClient> NewClient =
		MakeShared<FPoseGroupViewportClient>(PreviewScene.Get(), SharedThis(this));
	NewClient->SetGroupAsset(GroupAsset);
	NewClient->SetSelectedKey(SelectedKeyIndex);
	return NewClient;
}

void SPoseGroupViewport::AddReferencedObjects(FReferenceCollector& Collector)
{
	Collector.AddReferencedObject(PreviewComponent);
	Collector.AddReferencedObject(GroupAsset);
}
