// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Viewport/SPoseViewport.h"

#include "Pose/Data/PoseProxyAsset.h"
#include "Pose/Runtime/PoseComponent.h"
#include "Pose/Viewport/PoseViewportClient.h"

#include "AdvancedPreviewScene.h"
#include "SEditorViewport.h"

#define LOCTEXT_NAMESPACE "SPoseViewport"

const FName SPoseViewport::ViewportTabId(TEXT("PoseViewport"));

void SPoseViewport::Construct(const FArguments& InArgs)
{
	PoseAsset = InArgs._PoseAsset;

	// 预览场景自带地板与天光，够看清姿态，不需要额外布光。
	PreviewScene = MakeShared<FAdvancedPreviewScene>(FPreviewScene::ConstructionValues());

	PreviewComponent = NewObject<UPoseComponent>(GetTransientPackage());
	PreviewComponent->SetFlags(RF_Transient);
	PreviewScene->AddComponent(PreviewComponent, FTransform::Identity);

	// SAssetEditorViewport 负责建视口外壳，具体客户端由 MakeEditorViewportClient 提供。
	SEditorViewport::Construct(SEditorViewport::FArguments());

	RefreshPreview();
}

void SPoseViewport::SetPoseAsset(UPoseProxyAsset* InPoseAsset)
{
	PoseAsset = InPoseAsset;
	RefreshPreview();
}

void SPoseViewport::RefreshPreview()
{
	if (PreviewComponent == nullptr)
	{
		return;
	}

	// ApplyPoseAsset 内部已经一并还原材质，这里不需要再调一次。
	PreviewComponent->ApplyPoseAsset(PoseAsset);
}

TSharedRef<FEditorViewportClient> SPoseViewport::MakeEditorViewportClient()
{
	return MakeShared<FPoseViewportClient>(PreviewScene.Get(), SharedThis(this));
}

void SPoseViewport::AddReferencedObjects(FReferenceCollector& Collector)
{
	Collector.AddReferencedObject(PreviewComponent);
	Collector.AddReferencedObject(PoseAsset);
}

#undef LOCTEXT_NAMESPACE
