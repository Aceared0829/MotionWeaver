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

	SelectedKeyIndex = GroupAsset != nullptr && GroupAsset->Keys.Num() > 0 ? 0 : INDEX_NONE;
	SEditorViewport::Construct(SEditorViewport::FArguments());

	SyncPoseComponents();
	SetSelectedKey(SelectedKeyIndex);
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
	SelectedKeyIndex = InAsset != nullptr && InAsset->Keys.Num() > 0 ? 0 : INDEX_NONE;
	SyncPoseComponents();
	SetSelectedKey(SelectedKeyIndex);
}

void SPoseGroupViewport::SetSelectedKey(int32 KeyIndex)
{
	const bool bSelectionChanged = SelectedKeyIndex != KeyIndex;
	SelectedKeyIndex = KeyIndex;
	// 选中关键帧意味着要回到编辑视图；否则播放组件仍显示，
	// 关键帧组件被隐藏，gizmo 却还在操作看不见的组件。
	if (bSelectionChanged && bPlaybackVisible)
	{
		SetPlaybackVisible(false);
	}
	ApplySelectionHighlight();

	if (FPoseGroupViewportClient* ViewportClientPtr = GetPoseViewportClient())
	{
		ViewportClientPtr->SetSelectedKey(KeyIndex);
		ViewportClientPtr->SetPreviewComponent(GetSelectedComponent());
	}
}

void SPoseGroupViewport::RefreshFromAsset()
{
	SyncPoseComponents();
}

void SPoseGroupViewport::RefreshSelectedPose()
{
	if (GroupAsset == nullptr || !GroupAsset->Keys.IsValidIndex(SelectedKeyIndex))
	{
		return;
	}

	ApplyKeyToComponent(SelectedKeyIndex);
	ApplySelectionHighlight();

	if (FPoseGroupViewportClient* ViewportClientPtr = GetPoseViewportClient())
	{
		ViewportClientPtr->SetPreviewComponent(GetSelectedComponent());
		ViewportClientPtr->Invalidate();
	}
}

UPoseComponent* SPoseGroupViewport::CreatePoseComponent()
{
	if (!PreviewScene.IsValid())
	{
		return nullptr;
	}

	UPoseComponent* NewComponent = NewObject<UPoseComponent>(GetTransientPackage());
	if (NewComponent == nullptr)
	{
		return nullptr;
	}

	NewComponent->SetFlags(RF_Transient);
	NewComponent->SetMobility(EComponentMobility::Movable);
	NewComponent->SetCollisionEnabled(ECollisionEnabled::NoCollision);
	NewComponent->SetGenerateOverlapEvents(false);
	PreviewScene->AddComponent(NewComponent, FTransform::Identity);
	return NewComponent;
}

void SPoseGroupViewport::ApplyKeyToComponent(int32 KeyIndex)
{
	if (GroupAsset == nullptr
		|| !GroupAsset->Keys.IsValidIndex(KeyIndex)
		|| !PoseComponents.IsValidIndex(KeyIndex))
	{
		return;
	}

	UPoseComponent* Component = PoseComponents[KeyIndex];
	if (Component == nullptr)
	{
		return;
	}

	const FPoseKey& Key = GroupAsset->Keys[KeyIndex];

	// 位置与朝向由 key 决定；蒙皮网格本身不带根位移。
	Component->SetRelativeLocationAndRotation(Key.LocalLocation, Key.BodyRotation);

	const bool bHasMesh = Key.PoseAsset != nullptr && !Key.PoseAsset->PreviewMesh.IsNull();
	Component->SetVisibility(bHasMesh);

	if (bHasMesh)
	{
		Component->ApplyPoseAsset(Key.PoseAsset);
	}
}

void SPoseGroupViewport::SyncPoseComponents()
{
	if (!PreviewScene.IsValid())
	{
		return;
	}

	// 重建期间先把编辑模式手里的组件摘掉，避免销毁后留下悬空指针。
	if (FPoseGroupViewportClient* ViewportClientPtr = GetPoseViewportClient())
	{
		ViewportClientPtr->SetPreviewComponent(nullptr);
	}

	const int32 DesiredCount = GroupAsset != nullptr ? GroupAsset->Keys.Num() : 0;

	while (PoseComponents.Num() > DesiredCount)
	{
		TObjectPtr<UPoseComponent> Extra = PoseComponents.Pop();
		if (Extra != nullptr)
		{
			PreviewScene->RemoveComponent(Extra);
			Extra->DestroyComponent();
		}
	}

	while (PoseComponents.Num() < DesiredCount)
	{
		UPoseComponent* NewComponent = CreatePoseComponent();
		if (NewComponent == nullptr)
		{
			break;
		}
		PoseComponents.Add(NewComponent);
	}

	for (int32 KeyIndex = 0; KeyIndex < PoseComponents.Num(); ++KeyIndex)
	{
		ApplyKeyToComponent(KeyIndex);
	}

	ApplySelectionHighlight();
	// ApplyKeyToComponent 会按「有没有网格」重新显示关键帧组件；
	// 如果当前处于播放预览，关键帧仍应保持隐藏，否则新旧姿态会叠在一起。
	SetPlaybackVisible(bPlaybackVisible);

	if (FPoseGroupViewportClient* ViewportClientPtr = GetPoseViewportClient())
	{
		ViewportClientPtr->SetGroupAsset(GroupAsset);
		ViewportClientPtr->SetSelectedKey(SelectedKeyIndex);
		ViewportClientPtr->SetPreviewComponent(GetSelectedComponent());
	}
}

void SPoseGroupViewport::ApplySelectionHighlight()
{
	for (int32 KeyIndex = 0; KeyIndex < PoseComponents.Num(); ++KeyIndex)
	{
		UPoseComponent* Component = PoseComponents[KeyIndex];
		if (Component == nullptr)
		{
			continue;
		}

		// 选中帧保持实体并投影；其余帧用线框幽灵，避免和选中帧抢视觉焦点。
		const bool bSelected = KeyIndex == SelectedKeyIndex;
		Component->SetForceWireframe(!bSelected);
		Component->SetCastShadow(bSelected);
	}
}

UPoseComponent* SPoseGroupViewport::GetSelectedComponent() const
{
	return PoseComponents.IsValidIndex(SelectedKeyIndex)
		? PoseComponents[SelectedKeyIndex].Get()
		: nullptr;
}

TSharedRef<FEditorViewportClient> SPoseGroupViewport::MakeEditorViewportClient()
{
	TSharedRef<FPoseGroupViewportClient> NewClient =
		MakeShared<FPoseGroupViewportClient>(PreviewScene.Get(), SharedThis(this));
	NewClient->SetGroupAsset(GroupAsset);
	NewClient->SetSelectedKey(SelectedKeyIndex);

	// 预览组件就是被拖的对象：编辑模式把它交给 ApplyDeltaToComponent，
	// 所有副作用（标脏、通知移动、刷新面板）都是真发生的。
	NewClient->SetPreviewComponent(GetSelectedComponent());

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
	RefreshSelectedPose();
}

void SPoseGroupViewport::HandleKeyDragged(const FPoseKey& NewKeyValue)
{
	// 不回写预览组件：它的变换本来就是 ApplyDeltaToComponent 刚写好的，
	// 再拿 key 里的值覆盖一次会把它拉回上一帧的数，表现为拖动时往回荡。
	OnKeyDragged.ExecuteIfBound(NewKeyValue);
}

void SPoseGroupViewport::AddReferencedObjects(FReferenceCollector& Collector)
{
	for (TObjectPtr<UPoseComponent>& Component : PoseComponents)
	{
		Collector.AddReferencedObject(Component);
	}
	Collector.AddReferencedObject(PlaybackComponent);
	Collector.AddReferencedObject(AuthorComponent);
	Collector.AddReferencedObject(GroupAsset);
}

void SPoseGroupViewport::SetPlaybackFrames(const TArray<TArray<FTransform>>& LocalPerFrame)
{
	PlaybackFrames = LocalPerFrame;
	PlaybackFrameIndex = PlaybackFrames.Num() > 0 ? 0 : INDEX_NONE;

	if (!PreviewScene.IsValid())
	{
		return;
	}

	// 预览组件只在第一次播放时创建；之后复用，避免每播一次就新建一个蒙皮组件。
	if (PlaybackComponent == nullptr)
	{
		PlaybackComponent = CreatePoseComponent();
		if (PlaybackComponent == nullptr)
		{
			return;
		}
	}

	// 每次生成都重新应用关键帧的网格与材质：组件复用，但骨架/网格可能被作者换过。
	// 播放角色必须与关键帧姿态来自同一骨架，否则骨名对不上，播放出来是一具静止的参考姿态。
	if (GroupAsset != nullptr)
	{
		for (const FPoseKey& Key : GroupAsset->Keys)
		{
			if (Key.PoseAsset != nullptr
				&& !Key.PoseAsset->PreviewMesh.IsNull()
				&& Key.PoseAsset->IsValidPose())
			{
				PlaybackComponent->ApplyPoseAsset(Key.PoseAsset);
				PlaybackMeshAsset = Key.PoseAsset->PreviewMesh.LoadSynchronous();
				break;
			}
		}
	}

	SetPlaybackFrame(PlaybackFrameIndex);
}

void SPoseGroupViewport::SetAuthorFrames(const TArray<TArray<FTransform>>& LocalPerFrame)
{
	AuthorFrames = LocalPerFrame;
	if (AuthorFrames.Num() == 0)
	{
		ClearAuthorFrames();
		return;
	}
	if (bShowAuthorGhost)
	{
		ApplyAuthorFrame(PlaybackFrameIndex);
	}
}

void SPoseGroupViewport::ClearAuthorFrames()
{
	AuthorFrames.Reset();
	if (AuthorComponent != nullptr)
	{
		AuthorComponent->SetVisibility(false);
	}
}

UPoseComponent* SPoseGroupViewport::EnsureAuthorComponent()
{
	if (AuthorComponent == nullptr)
	{
		AuthorComponent = CreatePoseComponent();
		if (AuthorComponent != nullptr)
		{
			// 参考幽灵只做对照：线框 + 不投影，避免和实体互相遮挡。
			AuthorComponent->SetForceWireframe(true);
			AuthorComponent->SetCastShadow(false);
		}
	}
	if (AuthorComponent != nullptr)
	{
		// 网格必须跟随播放组件，否则两边骨架槽位对不上，幽灵会乱飞。
		USkeletalMesh* Mesh = PlaybackMeshAsset.Get();
		if (Mesh != nullptr)
		{
			AuthorComponent->SetSkinnedAssetAndUpdate(Mesh);
		}
	}
	return AuthorComponent;
}

void SPoseGroupViewport::ApplyAuthorFrame(int32 FrameIndex)
{
	if (!AuthorFrames.IsValidIndex(FrameIndex))
	{
		if (AuthorComponent != nullptr)
		{
			AuthorComponent->SetVisibility(false);
		}
		return;
	}
	UPoseComponent* Component = EnsureAuthorComponent();
	if (Component == nullptr)
	{
		return;
	}

	const TArray<FTransform>& Frame = AuthorFrames[FrameIndex];
	const int32 BoneCount = FMath::Min(Frame.Num(), Component->BoneSpaceTransforms.Num());
	for (int32 BoneIndex = 0; BoneIndex < BoneCount; ++BoneIndex)
	{
		Component->BoneSpaceTransforms[BoneIndex] = Frame[BoneIndex];
	}
	Component->MarkRefreshTransformDirty();
	Component->RefreshBoneTransforms();
	Component->SetVisibility(bShowAuthorGhost && bPlaybackVisible);
}

void SPoseGroupViewport::SetShowAuthorGhost(bool bShow)
{
	bShowAuthorGhost = bShow;
	if (!bShow)
	{
		if (AuthorComponent != nullptr)
		{
			AuthorComponent->SetVisibility(false);
		}
		return;
	}
	ApplyAuthorFrame(PlaybackFrameIndex);
}

void SPoseGroupViewport::ClearPlaybackFrames()
{
	PlaybackFrames.Reset();
	PlaybackFrameIndex = INDEX_NONE;
	bPlaybackVisible = false;
	if (PlaybackComponent != nullptr)
	{
		PlaybackComponent->SetVisibility(false);
	}
	if (AuthorComponent != nullptr)
	{
		AuthorComponent->SetVisibility(false);
	}
	SetPlaybackVisible(false);
}

void SPoseGroupViewport::SetPlaybackFrame(int32 FrameIndex)
{
	if (!PlaybackFrames.IsValidIndex(FrameIndex) || PlaybackComponent == nullptr)
	{
		return;
	}
	PlaybackFrameIndex = FrameIndex;

	const TArray<FTransform>& Frame = PlaybackFrames[FrameIndex];
	// PoseableMesh 的 BoneSpaceTransforms 就是组件空间的骨骼局部变换；
	// 按 UE 骨架的骨槽位写入，找不到的骨保持参考姿态。
	// 生成后又换了预览网格时，两边骨数可能不同；只写两边都存在的槽位，
	// 避免越界写进 BoneSpaceTransforms。
	const int32 BoneCount = FMath::Min(Frame.Num(), PlaybackComponent->BoneSpaceTransforms.Num());
	for (int32 BoneIndex = 0; BoneIndex < BoneCount; ++BoneIndex)
	{
		PlaybackComponent->BoneSpaceTransforms[BoneIndex] = Frame[BoneIndex];
	}
	// BoneSpaceTransforms 是父级相对（根骨相对组件）的局部变换；
	// 根骨已经在 Frame[0] 里带上了整段位移/朝向，不能再写组件变换，
	// 否则最终骨骼矩阵 = 组件变换 × 根局部变换会把根运动叠加两次。
	PlaybackComponent->MarkRefreshTransformDirty();
	PlaybackComponent->RefreshBoneTransforms();

	// 参考幽灵必须与生成帧同帧推进，否则对照的是两个时刻。
	if (bShowAuthorGhost)
	{
		ApplyAuthorFrame(FrameIndex);
	}
}

void SPoseGroupViewport::SetPlaybackVisible(bool bVisible)
{
	bPlaybackVisible = bVisible;
	for (TObjectPtr<UPoseComponent>& Component : PoseComponents)
	{
		if (Component != nullptr)
		{
			// 默认播放时隐藏关键帧；勾选「保留关键帧幽灵」后继续显示，
			// 方便作者直接对照「生成到哪一帧、原关键帧长什么样」。
			Component->SetVisibility(!bVisible || bKeepKeyframesDuringPlayback);
		}
	}
	if (PlaybackComponent != nullptr)
	{
		PlaybackComponent->SetVisibility(bVisible);
	}
	if (AuthorComponent != nullptr)
	{
		AuthorComponent->SetVisibility(bVisible && bShowAuthorGhost);
	}
}

void SPoseGroupViewport::SetPlaybackKeepKeyframes(bool bKeep)
{
	bKeepKeyframesDuringPlayback = bKeep;
	if (bPlaybackVisible)
	{
		// 立刻重算可见性，而不是等下一次播放。
		SetPlaybackVisible(true);
	}
}
