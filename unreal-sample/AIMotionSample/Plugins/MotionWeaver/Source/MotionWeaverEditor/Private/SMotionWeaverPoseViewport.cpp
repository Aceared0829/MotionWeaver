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

	PreviewComponent->ApplyPoseAsset(PoseAsset);

	// 与关卡实例共用同一份材质还原实现，保证预览和摆进关卡的画面一致。
	PreviewComponent->ApplyPreviewMaterials();
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
