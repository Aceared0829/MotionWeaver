// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Viewport/SPoseGroupViewport.h"

#include "Pose/Data/PoseGroupProxyAsset.h"
#include "Pose/Data/PoseProxyAsset.h"
#include "Pose/Runtime/PoseComponent.h"
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

FPoseGroupViewportClient* SPoseGroupViewport::GetPoseViewportClient() const
{
	// GetViewportClient 在 Construct 早期可能还没有：
	// 那时控件尚未完成初始化，不能强转后调用。
	const TSharedPtr<FEditorViewportClient> Handle = GetViewportClient();
	return Handle.IsValid() ? static_cast<FPoseGroupViewportClient*>(Handle.Get()) : nullptr;
}

void SPoseGroupViewport::SetGroupAsset(UPoseGroupProxyAsset* InAsset)
{
	GroupAsset = InAsset;

	if (FPoseGroupViewportClient* ViewportClientPtr = GetPoseViewportClient())
	{
		ViewportClientPtr->SetGroupAsset(InAsset);
	}

	SetSelectedKey(InAsset != nullptr && InAsset->Keys.Num() > 0 ? 0 : INDEX_NONE);
}

void SPoseGroupViewport::SetSelectedKey(int32 KeyIndex)
{
	SelectedKeyIndex = KeyIndex;

	if (FPoseGroupViewportClient* ViewportClientPtr = GetPoseViewportClient())
	{
		ViewportClientPtr->SetSelectedKey(KeyIndex);
		ViewportClientPtr->SetPreviewComponent(PreviewComponent);
	}

	ApplySelectedPose();
}

void SPoseGroupViewport::ApplySelectedPose()
{
	const bool bHasPreview = PreviewComponent != nullptr;

	// 没有可显示的帧就清掉姿态，别留下上一帧的影子。
	if (GroupAsset == nullptr || !GroupAsset->Keys.IsValidIndex(SelectedKeyIndex))
	{
		if (bHasPreview)
		{
			PreviewComponent->SetVisibility(false);
		}
		return;
	}

	if (!bHasPreview)
	{
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

	// 预览组件就是被拖的对象：编辑模式把它交给 ApplyDeltaToComponent，
	// 所有副作用（标脏、通知移动、刷新面板）都是真发生的。
	NewClient->SetPreviewComponent(PreviewComponent);

	// 模式拖出来的值回到编辑器：写资产、开合事务、松手求解都在那边。
	NewClient->SetOnKeyDragged(FPoseGroupViewportClient::FOnKeyDragged::CreateSP(
		this, &SPoseGroupViewport::HandleKeyDragged));

	NewClient->SetOnDragFinished(FPoseGroupViewportClient::FOnDragFinished::CreateSP(
		this, &SPoseGroupViewport::HandleDragFinished));

	return NewClient;
}

void SPoseGroupViewport::HandleDragFinished()
{
	OnDragFinished.ExecuteIfBound();

	// 松手后从资产重新读一次：拖动期间预览用的是组件当前值，
	// 资产可能已被求解改写，这里对齐一次避免残留中间态。
	ApplySelectedPose();
}

void SPoseGroupViewport::HandleKeyDragged(const FPoseKey& NewKeyValue)
{
	// 不回写预览组件：它的变换本来就是 ApplyDeltaToComponent 刚写好的，
	// 再拿 key 里的值覆盖一次会把它拉回上一帧的数，表现为拖动时往回荡。
	OnKeyDragged.ExecuteIfBound(NewKeyValue);
}

void SPoseGroupViewport::AddReferencedObjects(FReferenceCollector& Collector)
{
	Collector.AddReferencedObject(PreviewComponent);
	Collector.AddReferencedObject(GroupAsset);
}
