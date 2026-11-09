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

	// GetViewportClient 在 Construct 早期可能还没有：
	// 那时控件尚未完成初始化，不能强转后调用。
	if (const TSharedPtr<FEditorViewportClient> ViewportClientHandle = GetViewportClient())
	{
		if (FPoseGroupViewportClient* ViewportClientPtr =
			static_cast<FPoseGroupViewportClient*>(Client.Get()))
		{
			ViewportClientPtr->SetGroupAsset(InAsset);
		}
	}

	SetSelectedKey(InAsset != nullptr && InAsset->Keys.Num() > 0 ? 0 : INDEX_NONE);
}

void SPoseGroupViewport::SetSelectedKey(int32 KeyIndex)
{
	SelectedKeyIndex = KeyIndex;

	if (const TSharedPtr<FEditorViewportClient> ViewportClientHandle = GetViewportClient())
	{
		if (FPoseGroupViewportClient* ViewportClientPtr =
			static_cast<FPoseGroupViewportClient*>(ViewportClientHandle.Get()))
		{
			ViewportClientPtr->SetSelectedKey(KeyIndex);
			ViewportClientPtr->SetDragTarget(PreviewComponent);
		}
	}

	ApplySelectedPose();
}

void SPoseGroupViewport::ApplySelectedPose()
{
	const TSharedPtr<FEditorViewportClient> ViewportClientHandle = GetViewportClient();
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

	// 预览组件就是被拖的对象：引擎的 ApplyDeltaToComponent 直接作用在它身上，
	// 所有副作用（标脏、通知移动、刷新面板）都是真发生的。
	NewClient->SetDragTarget(PreviewComponent);

	// 拖拽结果交给外部（编辑器）：资产改动要走事务、要求解、要让时间轴刷新，
	// 那些都在编辑器里，视口只负责把新值算出来。
	NewClient->SetOnKeyDragged(FPoseGroupViewportClient::FOnKeyDragged::CreateSP(
		this, &SPoseGroupViewport::HandleKeyDragged));
	NewClient->SetOnDragStarted(FPoseGroupViewportClient::FOnDragStateChanged::CreateSP(
		this, &SPoseGroupViewport::HandleDragStarted));
	NewClient->SetOnDragFinished(FPoseGroupViewportClient::FOnDragStateChanged::CreateSP(
		this, &SPoseGroupViewport::HandleDragFinished));

	return NewClient;
}

void SPoseGroupViewport::HandleKeyDragged(const FPoseKey& NewKeyValue)
{
	// 这里不再回写预览组件。
	//
	// 组件的变换本来就是引擎 ApplyDeltaToComponent 刚写好的，
	// 再拿 key 里的值覆盖一次会把它拉回到「上一帧读出的那个数」，
	// 表现为拖动时角色一顿一顿地往回弹。显示已经是正确的，
	// 这里只把新值交给编辑器写进资产。
	OnKeyDragged.ExecuteIfBound(NewKeyValue);
}

void SPoseGroupViewport::HandleDragStarted()
{
	OnDragStarted.ExecuteIfBound();
}

void SPoseGroupViewport::HandleDragFinished()
{
	OnDragFinished.ExecuteIfBound();

	// 松手后从资产重新读一次：拖动期间预览用的是临时值，
	// 资产已经被编辑器写回并求解过，这里对齐一次避免残留中间态。
	ApplySelectedPose();
}

void SPoseGroupViewport::AddReferencedObjects(FReferenceCollector& Collector)
{
	Collector.AddReferencedObject(PreviewComponent);
	Collector.AddReferencedObject(GroupAsset);
}