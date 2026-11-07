// Copyright ZhaoZining. All Rights Reserved.

#include "SMotionWeaverPoseViewport.h"

#include "MotionWeaverPoseAsset.h"
#include "MotionWeaverPoseComponent.h"
#include "MotionWeaverPoseViewportClient.h"

#include "AdvancedPreviewScene.h"
#include "SEditorViewport.h"

#define LOCTEXT_NAMESPACE "SMotionWeaverPoseViewport"

const FName SMotionWeaverPoseViewport::ViewportTabId(TEXT("MotionWeaverPoseViewport"));

void SMotionWeaverPoseViewport::Construct(const FArguments& InArgs)
{
	PoseAsset = InArgs._PoseAsset;

	// 预览场景自带地板与天光，够看清姿态，不需要额外布光。
	PreviewScene = MakeShared<FAdvancedPreviewScene>(FPreviewScene::ConstructionValues());

	PreviewComponent = NewObject<UMotionWeaverPoseComponent>(GetTransientPackage());
	PreviewComponent->SetFlags(RF_Transient);
	PreviewScene->AddComponent(PreviewComponent, FTransform::Identity);

	// SAssetEditorViewport 负责建视口外壳，具体客户端由 MakeEditorViewportClient 提供。
	SEditorViewport::Construct(SEditorViewport::FArguments());

	RefreshPreview();
}

void SMotionWeaverPoseViewport::SetPoseAsset(UMotionWeaverPoseAsset* InPoseAsset)
{
	PoseAsset = InPoseAsset;
	RefreshPreview();
}

void SMotionWeaverPoseViewport::RefreshPreview()
{
	if (PreviewComponent == nullptr)
	{
		return;
	}

	// ApplyPoseAsset 内部已经一并还原材质，这里不需要再调一次。
	PreviewComponent->ApplyPoseAsset(PoseAsset);
}

TSharedRef<FEditorViewportClient> SMotionWeaverPoseViewport::MakeEditorViewportClient()
{
	return MakeShared<FMotionWeaverPoseViewportClient>(PreviewScene.Get(), SharedThis(this));
}

void SMotionWeaverPoseViewport::AddReferencedObjects(FReferenceCollector& Collector)
{
	Collector.AddReferencedObject(PreviewComponent);
	Collector.AddReferencedObject(PoseAsset);
}

#undef LOCTEXT_NAMESPACE
