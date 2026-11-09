// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Assets/PoseGroupProxyAssetEditor.h"

#include "Pose/PoseGroupProxyAsset.h"
#include "Pose/Viewport/SPoseKeyTimeline.h"
#include "Pose/Viewport/SPoseGroupViewport.h"

#include "Editor.h"
#include "Framework/Docking/TabManager.h"
#include "Modules/ModuleManager.h"
#include "PropertyEditorModule.h"
#include "ScopedTransaction.h"
#include "Widgets/Docking/SDockTab.h"

#define LOCTEXT_NAMESPACE "FPoseGroupProxyAssetEditor"

const FName FPoseGroupProxyAssetEditor::ViewportTabId(TEXT("PoseGroupAssetEditor_Viewport"));
const FName FPoseGroupProxyAssetEditor::TimelineTabId(TEXT("PoseGroupAssetEditor_Timeline"));
const FName FPoseGroupProxyAssetEditor::DetailsTabId(TEXT("PoseGroupAssetEditor_Details"));

void FPoseGroupProxyAssetEditor::InitPoseGroupProxyAssetEditor(
	const EToolkitMode::Type Mode, const TSharedPtr<IToolkitHost>& InitToolkitHost,
	UPoseGroupProxyAsset* InAsset)
{
	GroupAsset = InAsset;

	ViewportWidget = SNew(SPoseGroupViewport).GroupAsset(GroupAsset);

	// 视口的拖拽结果回到这里：写值、开合事务、松手求解。
	ViewportWidget->OnKeyDragged.BindSP(this, &FPoseGroupProxyAssetEditor::ApplyDraggedKey);
	ViewportWidget->OnDragStarted.BindSP(this, &FPoseGroupProxyAssetEditor::BeginDragTransaction);
	ViewportWidget->OnDragFinished.BindSP(this, &FPoseGroupProxyAssetEditor::EndDragTransaction);
	TimelineWidget = SNew(SPoseKeyTimeline).Editor(SharedThis(this));

	FPropertyEditorModule& PropertyEditorModule =
		FModuleManager::LoadModuleChecked<FPropertyEditorModule>("PropertyEditor");

	FDetailsViewArgs DetailsViewArgs;
	DetailsViewArgs.bAllowSearch = true;
	DetailsViewArgs.bHideSelectionTip = true;
	DetailsViewArgs.NameAreaSettings = FDetailsViewArgs::HideNameArea;

	DetailsView = PropertyEditorModule.CreateDetailView(DetailsViewArgs);
	DetailsView->SetObject(GroupAsset);

	// 左边视口占大头，中间时间轴，右边细节面板。
	const TSharedRef<FTabManager::FLayout> Layout =
		FTabManager::NewLayout("Standalone_PoseGroupAssetEditor_v1")
		->AddArea
		(
			FTabManager::NewPrimaryArea()
			->SetOrientation(Orient_Horizontal)
			->Split
			(
				FTabManager::NewStack()
				->SetSizeCoefficient(0.55f)
				->AddTab(ViewportTabId, ETabState::OpenedTab)
			)
			->Split
			(
				FTabManager::NewStack()
				->SetSizeCoefficient(0.20f)
				->AddTab(TimelineTabId, ETabState::OpenedTab)
			)
			->Split
			(
				FTabManager::NewStack()
				->SetSizeCoefficient(0.25f)
				->AddTab(DetailsTabId, ETabState::OpenedTab)
			)
		);

	InitAssetEditor(
		Mode, InitToolkitHost, TEXT("PoseGroupAssetEditor"), Layout,
		/*bCreateDefaultStandaloneMenu*/ true,
		/*bCreateDefaultToolbar*/ true,
		GroupAsset);

	RegenerateMenusAndToolbars();

	// 打开就选中第 0 帧，避免「什么都没选中」的空状态。
	SetSelectedKeyIndex(GroupAsset != nullptr && GroupAsset->Keys.Num() > 0 ? 0 : INDEX_NONE);
}

void FPoseGroupProxyAssetEditor::RegisterTabSpawners(const TSharedRef<FTabManager>& InTabManager)
{
	FAssetEditorToolkit::RegisterTabSpawners(InTabManager);

	InTabManager->RegisterTabSpawner(ViewportTabId, FOnSpawnTab::CreateSP(this, &FPoseGroupProxyAssetEditor::SpawnTab_Viewport))
		.SetDisplayName(LOCTEXT("ViewportTabLabel", "视口"))
		.SetGroup(WorkspaceMenuCategory.ToSharedRef());

	InTabManager->RegisterTabSpawner(TimelineTabId, FOnSpawnTab::CreateSP(this, &FPoseGroupProxyAssetEditor::SpawnTab_Timeline))
		.SetDisplayName(LOCTEXT("TimelineTabLabel", "时间轴"))
		.SetGroup(WorkspaceMenuCategory.ToSharedRef());

	InTabManager->RegisterTabSpawner(DetailsTabId, FOnSpawnTab::CreateSP(this, &FPoseGroupProxyAssetEditor::SpawnTab_Details))
		.SetDisplayName(LOCTEXT("DetailsTabLabel", "细节"))
		.SetGroup(WorkspaceMenuCategory.ToSharedRef());
}

void FPoseGroupProxyAssetEditor::UnregisterTabSpawners(const TSharedRef<FTabManager>& InTabManager)
{
	FAssetEditorToolkit::UnregisterTabSpawners(InTabManager);

	InTabManager->UnregisterTabSpawner(ViewportTabId);
	InTabManager->UnregisterTabSpawner(TimelineTabId);
	InTabManager->UnregisterTabSpawner(DetailsTabId);
}

TSharedRef<SDockTab> FPoseGroupProxyAssetEditor::SpawnTab_Viewport(const FSpawnTabArgs& Args)
{
	ensureMsgf(ViewportWidget.IsValid(), TEXT("ViewportWidget 未在 Init 阶段创建。"));
	return SNew(SDockTab).Label(LOCTEXT("ViewportTabTitle", "姿态视口"))
		[
			ViewportWidget.ToSharedRef()
		];
}

TSharedRef<SDockTab> FPoseGroupProxyAssetEditor::SpawnTab_Timeline(const FSpawnTabArgs& Args)
{
	ensureMsgf(TimelineWidget.IsValid(), TEXT("TimelineWidget 未在 Init 阶段创建。"));
	return SNew(SDockTab).Label(LOCTEXT("TimelineTabTitle", "关键帧"))
		[
			TimelineWidget.ToSharedRef()
		];
}

TSharedRef<SDockTab> FPoseGroupProxyAssetEditor::SpawnTab_Details(const FSpawnTabArgs& Args)
{
	ensureMsgf(DetailsView.IsValid(), TEXT("DetailsView 未在 Init 阶段创建。"));
	return SNew(SDockTab).Label(LOCTEXT("DetailsTabTitle", "数据"))
		[
			DetailsView.ToSharedRef()
		];
}

void FPoseGroupProxyAssetEditor::SetSelectedKeyIndex(int32 InKeyIndex)
{
	const int32 MaxIndex = GroupAsset != nullptr ? GroupAsset->Keys.Num() - 1 : INDEX_NONE;

	// 越界时收敛到合法范围，不要留下一个「选了个不存在的帧」的状态。
	SelectedKeyIndex = (InKeyIndex >= 0 && InKeyIndex <= MaxIndex) ? InKeyIndex : INDEX_NONE;

	if (ViewportWidget.IsValid())
	{
		ViewportWidget->SetSelectedKey(SelectedKeyIndex);
	}

	if (TimelineWidget.IsValid())
	{
		TimelineWidget->SetSelectedIndex(SelectedKeyIndex);
	}
}

void FPoseGroupProxyAssetEditor::RefreshFromAsset()
{
	if (TimelineWidget.IsValid())
	{
		TimelineWidget->RefreshRows();
	}

	SetSelectedKeyIndex(SelectedKeyIndex);

	if (DetailsView.IsValid())
	{
		DetailsView->SetObject(GroupAsset, true);
	}
}

void FPoseGroupProxyAssetEditor::AddKey()
{
	if (GroupAsset == nullptr)
	{
		return;
	}

	const FScopedTransaction Transaction(LOCTEXT("AddPoseKey", "添加关键帧"));
	GroupAsset->Modify();

	FPoseKey NewKey;

	// 帧号接在最后一个之后。用 Settings.NewKeyFrameStride 而不是硬编码 24：
	// 这个值本来就是给「新帧间隔」用的，作者改过就该尊重。
	if (GroupAsset->Keys.Num() > 0)
	{
		NewKey.Frame = GroupAsset->Keys.Last().Frame
			+ FMath::Max(1, GroupAsset->Settings.NewKeyFrameStride);
	}

	GroupAsset->Keys.Add(NewKey);
	GroupAsset->Solve();
	GroupAsset->MarkPackageDirty();

	const int32 NewIndex = GroupAsset->Keys.Num() - 1;
	RefreshFromAsset();
	SetSelectedKeyIndex(NewIndex);
}

void FPoseGroupProxyAssetEditor::DeleteSelectedKey()
{
	if (GroupAsset == nullptr || !GroupAsset->Keys.IsValidIndex(SelectedKeyIndex))
	{
		return;
	}

	const FScopedTransaction Transaction(LOCTEXT("DeletePoseKey", "删除关键帧"));
	GroupAsset->Modify();

	GroupAsset->Keys.RemoveAt(SelectedKeyIndex);
	GroupAsset->Solve();
	GroupAsset->MarkPackageDirty();

	RefreshFromAsset();

	// 删完收敛到前一个，删的是最后一个就退一格，没有帧了就清空选择。
	SetSelectedKeyIndex(FMath::Min(SelectedKeyIndex, GroupAsset->Keys.Num() - 1));
}

void FPoseGroupProxyAssetEditor::SetSelectedKeyFrame(int32 NewFrame)
{
	if (GroupAsset == nullptr || !GroupAsset->Keys.IsValidIndex(SelectedKeyIndex))
	{
		return;
	}

	if (NewFrame < 0 || NewFrame == GroupAsset->Keys[SelectedKeyIndex].Frame)
	{
		return;
	}

	// 帧号必须严格递增：这里不静默纠正，而是拒绝并保持原值。
	// 悄悄改掉作者刚输入的数是更糟的体验——他会以为改成功了。
	const bool bHasPrev = SelectedKeyIndex > 0;
	const bool bHasNext = GroupAsset->Keys.IsValidIndex(SelectedKeyIndex + 1);

	if (bHasPrev && NewFrame <= GroupAsset->Keys[SelectedKeyIndex - 1].Frame)
	{
		return;
	}
	if (bHasNext && NewFrame >= GroupAsset->Keys[SelectedKeyIndex + 1].Frame)
	{
		return;
	}

	const FScopedTransaction Transaction(LOCTEXT("EditPoseKeyFrame", "修改关键帧帧号"));
	GroupAsset->Modify();

	GroupAsset->Keys[SelectedKeyIndex].Frame = NewFrame;
	GroupAsset->Solve();
	GroupAsset->MarkPackageDirty();

	RefreshFromAsset();
}

void FPoseGroupProxyAssetEditor::ApplyDraggedKey(const FPoseKey& NewKeyValue)
{
	if (GroupAsset == nullptr || !GroupAsset->Keys.IsValidIndex(SelectedKeyIndex))
	{
		return;
	}

	FPoseKey& Key = GroupAsset->Keys[SelectedKeyIndex];

	// 数值没变就不重算：拖拽每帧都会回调，无差别求解会把拖动拖成卡顿。
	const bool bChanged =
		!Key.LocalLocation.Equals(NewKeyValue.LocalLocation, 0.01) ||
		!Key.BodyRotation.Equals(NewKeyValue.BodyRotation, 0.05);

	if (!bChanged)
	{
		return;
	}

	Key.LocalLocation = NewKeyValue.LocalLocation;
	Key.SetBodyRotation(NewKeyValue.BodyRotation);

	// 拖拽期间不重解整条链——作者还在动，只有松手才需要最终结果。
	// 但资产要标脏，松手时统一 Solve。
	GroupAsset->MarkPackageDirty();
}

void FPoseGroupProxyAssetEditor::BeginDragTransaction()
{
	if (GroupAsset == nullptr)
	{
		return;
	}

	// 整段拖动一个事务：撤销一次回到拖动前，而不是退一帧。
	if (!bDragTransactionOpen)
	{
		GEditor->BeginTransaction(LOCTEXT("DragPoseKey", "拖动关键帧"));
		GroupAsset->Modify();
		bDragTransactionOpen = true;
	}
}

void FPoseGroupProxyAssetEditor::EndDragTransaction()
{
	if (bDragTransactionOpen)
	{
		GEditor->EndTransaction();
		bDragTransactionOpen = false;
	}

	// 松手才求解：拖动过程中重算会让手感发涩，而且中间结果没人看。
	if (GroupAsset != nullptr)
	{
		GroupAsset->Solve();
		RefreshFromAsset();
	}
}

FName FPoseGroupProxyAssetEditor::GetToolkitFName() const
{
	return FName("PoseGroupProxyAssetEditor");
}

FText FPoseGroupProxyAssetEditor::GetBaseToolkitName() const
{
	return LOCTEXT("AppLabel", "姿态代理组编辑器");
}

FText FPoseGroupProxyAssetEditor::GetToolkitName() const
{
	return FText::FromString(GroupAsset != nullptr ? GroupAsset->GetName() : TEXT("Pose Group"));
}

FString FPoseGroupProxyAssetEditor::GetWorldCentricTabPrefix() const
{
	return LOCTEXT("WorldCentricTabPrefix", "Pose Group ").ToString();
}

FLinearColor FPoseGroupProxyAssetEditor::GetWorldCentricTabColorScale() const
{
	return FLinearColor(1.0f, 0.67f, 0.18f, 0.5f);
}

#undef LOCTEXT_NAMESPACE
