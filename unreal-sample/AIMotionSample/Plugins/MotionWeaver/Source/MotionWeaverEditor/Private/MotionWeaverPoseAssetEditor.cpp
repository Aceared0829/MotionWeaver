// Copyright ZhaoZining. All Rights Reserved.

#include "MotionWeaverPoseAssetEditor.h"

#include "MotionWeaverPoseAsset.h"
#include "SMotionWeaverPoseViewport.h"

#include "Framework/Docking/TabManager.h"
#include "Modules/ModuleManager.h"
#include "PropertyEditorModule.h"
#include "Widgets/Docking/SDockTab.h"

#define LOCTEXT_NAMESPACE "FMotionWeaverPoseAssetEditor"

const FName FMotionWeaverPoseAssetEditor::ViewportTabId(TEXT("MotionWeaverPoseAssetEditor_Viewport"));
const FName FMotionWeaverPoseAssetEditor::DetailsTabId(TEXT("MotionWeaverPoseAssetEditor_Details"));

void FMotionWeaverPoseAssetEditor::InitMotionWeaverPoseAssetEditor(
	const EToolkitMode::Type Mode, const TSharedPtr<IToolkitHost>& InitToolkitHost, UMotionWeaverPoseAsset* InPoseAsset)
{
	PoseAsset = InPoseAsset;

	// 先建控件，tab 回调里会用到。
	ViewportWidget = SNew(SMotionWeaverPoseViewport).PoseAsset(PoseAsset);

	FPropertyEditorModule& PropertyEditorModule =
		FModuleManager::LoadModuleChecked<FPropertyEditorModule>("PropertyEditor");

	FDetailsViewArgs DetailsViewArgs;
	DetailsViewArgs.bAllowSearch = true;
	DetailsViewArgs.bHideSelectionTip = true;
	DetailsViewArgs.NameAreaSettings = FDetailsViewArgs::HideNameArea;

	DetailsView = PropertyEditorModule.CreateDetailView(DetailsViewArgs);
	DetailsView->SetObject(PoseAsset);

	// 左边视口占大头，右边细节面板。
	const TSharedRef<FTabManager::FLayout> Layout =
		FTabManager::NewLayout("Standalone_MotionWeaverPoseAssetEditor_v1")
		->AddArea
		(
			FTabManager::NewPrimaryArea()
			->SetOrientation(Orient_Horizontal)
			->Split
			(
				FTabManager::NewStack()
				->SetSizeCoefficient(0.65f)
				->AddTab(ViewportTabId, ETabState::OpenedTab)
			)
			->Split
			(
				FTabManager::NewStack()
				->SetSizeCoefficient(0.35f)
				->AddTab(DetailsTabId, ETabState::OpenedTab)
			)
		);

	InitAssetEditor(
		Mode,
		InitToolkitHost,
		TEXT("MotionWeaverPoseAssetEditor"),
		Layout,
		/*bCreateDefaultStandaloneMenu*/ true,
		/*bCreateDefaultToolbar*/ true,
		PoseAsset);

	RegenerateMenusAndToolbars();
}

void FMotionWeaverPoseAssetEditor::RegisterTabSpawners(const TSharedRef<FTabManager>& InTabManager)
{
	FAssetEditorToolkit::RegisterTabSpawners(InTabManager);

	InTabManager->RegisterTabSpawner(ViewportTabId, FOnSpawnTab::CreateSP(this, &FMotionWeaverPoseAssetEditor::SpawnTab_Viewport))
		.SetDisplayName(LOCTEXT("ViewportTabLabel", "视口"))
		.SetGroup(WorkspaceMenuCategory.ToSharedRef());

	InTabManager->RegisterTabSpawner(DetailsTabId, FOnSpawnTab::CreateSP(this, &FMotionWeaverPoseAssetEditor::SpawnTab_Details))
		.SetDisplayName(LOCTEXT("DetailsTabLabel", "细节"))
		.SetGroup(WorkspaceMenuCategory.ToSharedRef());
}

void FMotionWeaverPoseAssetEditor::UnregisterTabSpawners(const TSharedRef<FTabManager>& InTabManager)
{
	FAssetEditorToolkit::UnregisterTabSpawners(InTabManager);

	InTabManager->UnregisterTabSpawner(ViewportTabId);
	InTabManager->UnregisterTabSpawner(DetailsTabId);
}

TSharedRef<SDockTab> FMotionWeaverPoseAssetEditor::SpawnTab_Viewport(const FSpawnTabArgs& Args)
{
	return SNew(SDockTab)
		.Label(LOCTEXT("ViewportTabTitle", "姿态视口"))
		[
			ViewportWidget.ToSharedRef()
		];
}

TSharedRef<SDockTab> FMotionWeaverPoseAssetEditor::SpawnTab_Details(const FSpawnTabArgs& Args)
{
	return SNew(SDockTab)
		.Label(LOCTEXT("DetailsTabTitle", "姿势数据"))
		[
			DetailsView.ToSharedRef()
		];
}

FName FMotionWeaverPoseAssetEditor::GetToolkitFName() const
{
	return FName("MotionWeaverPoseAssetEditor");
}

FText FMotionWeaverPoseAssetEditor::GetBaseToolkitName() const
{
	return LOCTEXT("AppLabel", "MotionWeaver Pose Editor");
}

FText FMotionWeaverPoseAssetEditor::GetToolkitName() const
{
	return FText::FromString(PoseAsset != nullptr ? PoseAsset->GetName() : TEXT("Pose"));
}

FString FMotionWeaverPoseAssetEditor::GetWorldCentricTabPrefix() const
{
	return LOCTEXT("WorldCentricTabPrefix", "Pose ").ToString();
}

FLinearColor FMotionWeaverPoseAssetEditor::GetWorldCentricTabColorScale() const
{
	return FLinearColor(0.3f, 0.5f, 0.9f, 0.5f);
}

#undef LOCTEXT_NAMESPACE
