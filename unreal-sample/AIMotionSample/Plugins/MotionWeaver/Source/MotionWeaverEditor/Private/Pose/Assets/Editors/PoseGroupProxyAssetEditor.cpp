// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Assets/Editors/PoseGroupProxyAssetEditor.h"

#include "Pose/Data/PoseGroupProxyAsset.h"
#include "Pose/Elements/PoseKeyAssetEditorContext.h"
#include "Pose/Elements/PoseKeyElementWorldInterface.h"
#include "Core/MotionWeaverLog.h"
#include "EditorModeManager.h"
#include "Pose/Viewport/SPoseKeyTimeline.h"
#include "Pose/Viewport/SPoseGroupViewport.h"

#include "Containers/Ticker.h"
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

	// 选择集提供者：变换手柄通过它找到「当前要操作哪一帧」。
	// Outer 用资产而不是 this：FAssetEditorToolkit 不是 UObject，不能当 Outer；
	// 挂在资产上还有个好处——资产一关，这个对象跟着回收。
	if (GroupAsset != nullptr)
	{
		ElementContext = NewObject<UPoseKeyAssetEditorContext>(GroupAsset);
		ElementContext->SetAsset(GroupAsset);
	}

	BindElementMovement();

	// 订阅撤销/重做：撤销改的是资产数据，视图不会自动知道。
	// 引擎的广播点是 FEditorDelegates::PostUndoRedo（Editor.h:235），
	// 不是 GEditor 上的成员。
	{
		TWeakPtr<FPoseGroupProxyAssetEditor> WeakSelf = SharedThis(this);

		UndoRedoHandle = FEditorDelegates::PostUndoRedo.AddLambda([WeakSelf]()
		{
			if (const TSharedPtr<FPoseGroupProxyAssetEditor> Pinned = WeakSelf.Pin())
			{
				Pinned->OnPostUndoRedo();
			}
		});
	}

	ViewportWidget = SNew(SPoseGroupViewport).GroupAsset(GroupAsset);

	// 视口的拖拽结果回到这里：写值、开合事务、松手求解。
	// 只接拖拽结果。事务与操纵状态现在由 FPoseGroupEditMode 负责
	// （StartTracking/EndTracking），这里不再重复开合。
	ViewportWidget->OnKeyDragged.BindSP(this, &FPoseGroupProxyAssetEditor::ApplyDraggedKey);
	ViewportWidget->OnDragFinished.BindSP(this, &FPoseGroupProxyAssetEditor::FinishDrag);
	TimelineWidget = SNew(SPoseKeyTimeline).Editor(SharedThis(this));

	FPropertyEditorModule& PropertyEditorModule =
		FModuleManager::LoadModuleChecked<FPropertyEditorModule>("PropertyEditor");

	FDetailsViewArgs DetailsViewArgs;
	DetailsViewArgs.bAllowSearch = true;
	DetailsViewArgs.bHideSelectionTip = true;
	DetailsViewArgs.NameAreaSettings = FDetailsViewArgs::HideNameArea;

	DetailsView = PropertyEditorModule.CreateDetailView(DetailsViewArgs);
	DetailsView->SetObject(GroupAsset);
	DetailsPropertyHandle = DetailsView->OnFinishedChangingProperties().AddSP(this, &FPoseGroupProxyAssetEditor::OnAssetPropertyChanged);

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

	// 播放时钟：只在编辑器打开期间存在，关闭时解绑。
	// 用 ticker 而不是给控件加 Tick：播放是编辑器状态，不属于某个控件的绘制周期。
	PlaybackTickerHandle = FTSTicker::GetCoreTicker().AddTicker(
		FTickerDelegate::CreateSP(this, &FPoseGroupProxyAssetEditor::TickPlaybackTick));

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
	const int32 NewIndex =
		(InKeyIndex >= 0 && InKeyIndex <= MaxIndex) ? InKeyIndex : INDEX_NONE;
	const bool bSelectionChanged = SelectedKeyIndex != NewIndex;
	SelectedKeyIndex = NewIndex;

	// 播放中切帧：视口会退出播放预览，但播放时钟在编辑器这一侧。
	// 不同时停掉的话，bIsPlaying 仍在后台推进，状态文字会继续报「播放中」，
	// 而画面上已经回到关键帧——两边各说各话。
	if (bSelectionChanged
		&& (bIsPlaying || (ViewportWidget.IsValid() && ViewportWidget->IsPlaybackVisible())))
	{
		StopPlayback();
	}

	if (ViewportWidget.IsValid())
	{
		ViewportWidget->SetSelectedKey(SelectedKeyIndex);
	}

	if (TimelineWidget.IsValid())
	{
		TimelineWidget->SetSelectedIndex(SelectedKeyIndex);
	}

	// 让选择集跟着选中帧走：手柄据此判断该操作哪一帧。
	if (ElementContext != nullptr)
	{
		ElementContext->SetSelectedKey(SelectedKeyIndex);
	}
}

void FPoseGroupProxyAssetEditor::RefreshFromAsset()
{
	if (ViewportWidget.IsValid())
	{
		ViewportWidget->RefreshFromAsset();
	}

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

void FPoseGroupProxyAssetEditor::OnAssetPropertyChanged(const FPropertyChangedEvent&)
{
	// 细节面板直接改资产，视口与时间轴不会自己刷新。
	// 这里不调 RefreshFromAsset：那会 SetObject 重建细节面板，
	// 作者正在输入的控件会被打断。
	if (GroupAsset == nullptr)
	{
		return;
	}

	// 资产内容变了，旧生成结果不再对应当前关键帧/轨迹。
	MarkGenerationStale();

	if (ViewportWidget.IsValid())
	{
		ViewportWidget->RefreshFromAsset();
	}

	if (TimelineWidget.IsValid())
	{
		TimelineWidget->RefreshRows();
	}

	// 行对象被重建后选中态会丢，重新按当前下标选一次。
	SetSelectedKeyIndex(SelectedKeyIndex);
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
	MarkGenerationStale();

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
	MarkGenerationStale();

	RefreshFromAsset();

	// 删完收敛到前一个，删的是最后一个就退一格，没有帧了就清空选择。
	SetSelectedKeyIndex(FMath::Min(SelectedKeyIndex, GroupAsset->Keys.Num() - 1));
}

void FPoseGroupProxyAssetEditor::SetSelectedKeyFrame(int32 NewFrame)
{
	SetKeyFrame(SelectedKeyIndex, NewFrame);
}

void FPoseGroupProxyAssetEditor::SetKeyTimeSeconds(int32 KeyIndex, double Seconds)
{
	if (GroupAsset == nullptr || !GroupAsset->Keys.IsValidIndex(KeyIndex))
	{
		return;
	}
	if (GroupAsset->Settings.FrameRate <= 0.0)
	{
		bLastGenerationFailed = true;
		GenerationStatus = TEXT("帧率必须大于 0，无法按秒换算帧号。");
		return;
	}
	const double FrameValue = Seconds * GroupAsset->Settings.FrameRate;
	if (!FMath::IsFinite(FrameValue)
		|| FrameValue < 0.0
		|| FrameValue > static_cast<double>(TNumericLimits<int32>::Max()))
	{
		bLastGenerationFailed = true;
		GenerationStatus = TEXT("秒数换算后的帧号超出范围，已保持原值。");
		return;
	}
	SetKeyFrame(
		KeyIndex,
		FMath::RoundToInt(FrameValue));
}

void FPoseGroupProxyAssetEditor::SetKeyFrame(int32 KeyIndex, int32 NewFrame)
{
	if (GroupAsset == nullptr || !GroupAsset->Keys.IsValidIndex(KeyIndex))
	{
		return;
	}
	if (NewFrame < 0)
	{
		bLastGenerationFailed = true;
		GenerationStatus = FString::Printf(TEXT("帧号不能为负：%d。"), NewFrame);
		return;
	}
	if (NewFrame == GroupAsset->Keys[KeyIndex].Frame)
	{
		return;
	}

	// 帧号必须严格递增：这里不静默纠正，而是拒绝并保持原值。
	// 悄悄改掉作者刚输入的数是更糟的体验——他会以为改成功了。
	const bool bHasPrev = KeyIndex > 0;
	const bool bHasNext = GroupAsset->Keys.IsValidIndex(KeyIndex + 1);

	if (bHasPrev && NewFrame <= GroupAsset->Keys[KeyIndex - 1].Frame)
	{
		bLastGenerationFailed = true;
		GenerationStatus = FString::Printf(
			TEXT("帧号必须严格递增：%d 必须大于前一帧 %d。"),
			NewFrame, GroupAsset->Keys[KeyIndex - 1].Frame);
		return;
	}
	if (bHasNext && NewFrame >= GroupAsset->Keys[KeyIndex + 1].Frame)
	{
		bLastGenerationFailed = true;
		GenerationStatus = FString::Printf(
			TEXT("帧号必须严格递增：%d 必须小于后一帧 %d。"),
			NewFrame, GroupAsset->Keys[KeyIndex + 1].Frame);
		return;
	}

	const FScopedTransaction Transaction(LOCTEXT("EditPoseKeyFrame", "修改关键帧帧号"));
	GroupAsset->Modify();

	GroupAsset->Keys[KeyIndex].Frame = NewFrame;
	GroupAsset->Solve();
	GroupAsset->MarkPackageDirty();
	MarkGenerationStale();

	bLastGenerationFailed = false;
	GenerationStatus = FString::Printf(
		TEXT("关键帧 %d 已改为 %.3f s（帧 %d）。"),
		KeyIndex,
		GroupAsset->Settings.FrameRate > 0.0
			? static_cast<double>(NewFrame) / GroupAsset->Settings.FrameRate
			: 0.0,
		NewFrame);
	if (bGenerationStale)
	{
		// MarkGenerationStale 的通用文案会被上面的具体改动覆盖；
		// 这里显式补回「已过期」，否则状态条只剩「改成功了」。
		GenerationStatus += TEXT("｜结果已过期，请重新生成。");
	}

	RefreshFromAsset();
}


void FPoseGroupProxyAssetEditor::OnClose()
{
	// 播放时钟必须在这里停掉：编辑器关掉后 ticker 仍会持有 this。
	if (PlaybackTickerHandle.IsValid())
	{
		FTSTicker::GetCoreTicker().RemoveTicker(PlaybackTickerHandle);
		PlaybackTickerHandle.Reset();
	}

	// 元素层委托是静态的，不解绑会留下悬空回调。
	UnbindElementMovement();

	if (UndoRedoHandle.IsValid())
	{
		FEditorDelegates::PostUndoRedo.Remove(UndoRedoHandle);
		UndoRedoHandle.Reset();
	}

	if (DetailsPropertyHandle.IsValid() && DetailsView.IsValid())
	{
		DetailsView->OnFinishedChangingProperties().Remove(DetailsPropertyHandle);
		DetailsPropertyHandle.Reset();
	}

	// 清掉选择集里那个元素句柄，避免编辑器关掉后 owner 悬着。
	if (ElementContext != nullptr)
	{
		ElementContext->SetSelectedKey(INDEX_NONE);
	}

	FAssetEditorToolkit::OnClose();
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
	return FText::FromString(
		GroupAsset != nullptr ? GroupAsset->GetName() : TEXT("姿态代理组"));
}

FString FPoseGroupProxyAssetEditor::GetWorldCentricTabPrefix() const
{
	return LOCTEXT("WorldCentricTabPrefix", "姿态代理组 ").ToString();
}

FLinearColor FPoseGroupProxyAssetEditor::GetWorldCentricTabColorScale() const
{
	return FLinearColor(1.0f, 0.67f, 0.18f, 0.5f);
}

#undef LOCTEXT_NAMESPACE
