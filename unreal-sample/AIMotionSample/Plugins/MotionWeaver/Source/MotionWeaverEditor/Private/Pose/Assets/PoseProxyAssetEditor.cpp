// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Assets/PoseProxyAssetEditor.h"

#include "Pose/PoseProxyAsset.h"
#include "Pose/Viewport/SPoseViewport.h"

#include "Framework/Docking/TabManager.h"
#include "Modules/ModuleManager.h"
#include "PropertyEditorModule.h"
#include "Widgets/Docking/SDockTab.h"

#define LOCTEXT_NAMESPACE "FPoseProxyAssetEditor"

const FName FPoseProxyAssetEditor::ViewportTabId(TEXT("MotionWeaverPoseAssetEditor_Viewport"));
const FName FPoseProxyAssetEditor::DetailsTabId(TEXT("MotionWeaverPoseAssetEditor_Details"));

void FPoseProxyAssetEditor::InitPoseProxyAssetEditor(
	const EToolkitMode::Type Mode, const TSharedPtr<IToolkitHost>& InitToolkitHost, UPoseProxyAsset* InPoseAsset)
{
	PoseAsset = InPoseAsset;

	// 先建控件，tab 回调里会用到。
	ViewportWidget = SNew(SPoseViewport).PoseAsset(PoseAsset);

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
		TEXT("PoseProxyAssetEditor"),
		Layout,
		/*bCreateDefaultStandaloneMenu*/ true,
		/*bCreateDefaultToolbar*/ true,
		PoseAsset);

	RegenerateMenusAndToolbars();
}

void FPoseProxyAssetEditor::RegisterTabSpawners(const TSharedRef<FTabManager>& InTabManager)
{
	FAssetEditorToolkit::RegisterTabSpawners(InTabManager);

	InTabManager->RegisterTabSpawner(ViewportTabId, FOnSpawnTab::CreateSP(this, &FPoseProxyAssetEditor::SpawnTab_Viewport))
		.SetDisplayName(LOCTEXT("ViewportTabLabel", "视口"))
		.SetGroup(WorkspaceMenuCategory.ToSharedRef());

	InTabManager->RegisterTabSpawner(DetailsTabId, FOnSpawnTab::CreateSP(this, &FPoseProxyAssetEditor::SpawnTab_Details))
		.SetDisplayName(LOCTEXT("DetailsTabLabel", "细节"))
		.SetGroup(WorkspaceMenuCategory.ToSharedRef());
}

void FPoseProxyAssetEditor::UnregisterTabSpawners(const TSharedRef<FTabManager>& InTabManager)
{
	FAssetEditorToolkit::UnregisterTabSpawners(InTabManager);

	InTabManager->UnregisterTabSpawner(ViewportTabId);
	InTabManager->UnregisterTabSpawner(DetailsTabId);
}

TSharedRef<SDockTab> FPoseProxyAssetEditor::SpawnTab_Viewport(const FSpawnTabArgs& Args)
{
	// 控件在 Init 阶段无条件创建；这里断言是为了将来把创建挪进条件分支时能立刻暴露。
	ensureMsgf(ViewportWidget.IsValid(), TEXT("ViewportWidget 未在 Init 阶段创建。"));

	return SNew(SDockTab)
		.Label(LOCTEXT("ViewportTabTitle", "姿态视口"))
		[
			ViewportWidget.ToSharedRef()
		];
}

TSharedRef<SDockTab> FPoseProxyAssetEditor::SpawnTab_Details(const FSpawnTabArgs& Args)
{
	ensureMsgf(DetailsView.IsValid(), TEXT("DetailsView 未在 Init 阶段创建。"));

	return SNew(SDockTab)
		.Label(LOCTEXT("DetailsTabTitle", "姿势数据"))
		[
			DetailsView.ToSharedRef()
		];
}

FName FPoseProxyAssetEditor::GetToolkitFName() const
{
	return FName("PoseProxyAssetEditor");
}

FText FPoseProxyAssetEditor::GetBaseToolkitName() const
{
	return LOCTEXT("AppLabel", "MotionWeaver Pose Editor");
}

FText FPoseProxyAssetEditor::GetToolkitName() const
{
	return FText::FromString(PoseAsset != nullptr ? PoseAsset->GetName() : TEXT("Pose"));
}

FString FPoseProxyAssetEditor::GetWorldCentricTabPrefix() const
{
	return LOCTEXT("WorldCentricTabPrefix", "Pose ").ToString();
}

FLinearColor FPoseProxyAssetEditor::GetWorldCentricTabColorScale() const
{
	return FLinearColor(0.3f, 0.5f, 0.9f, 0.5f);
}

#undef LOCTEXT_NAMESPACE
