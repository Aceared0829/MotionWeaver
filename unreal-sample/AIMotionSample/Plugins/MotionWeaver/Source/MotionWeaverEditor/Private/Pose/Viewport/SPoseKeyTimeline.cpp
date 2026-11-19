// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Viewport/SPoseKeyTimeline.h"

#include "Pose/Assets/Editors/PoseGroupProxyAssetEditor.h"
#include "Pose/Data/PoseGroupProxyAsset.h"
#include "Pose/Data/PoseProxyAsset.h"

#include "Misc/DefaultValueHelper.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Input/SEditableTextBox.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/SBoxPanel.h"
#include "Widgets/Text/STextBlock.h"

#define LOCTEXT_NAMESPACE "SPoseKeyTimeline"

void SPoseKeyTimeline::Construct(const FArguments& InArgs)
{
	EditorPtr = InArgs._Editor;

	ChildSlot
	[
		SNew(SVerticalBox)

		+ SVerticalBox::Slot()
		.AutoHeight()
		.Padding(4.0f)
		[
			SNew(SHorizontalBox)

			+ SHorizontalBox::Slot()
			.AutoWidth()
			.Padding(0.0f, 0.0f, 4.0f, 0.0f)
			[
				SNew(SButton)
				.Text(LOCTEXT("AddKey", "添加帧"))
				.ToolTipText(LOCTEXT("AddKeyTip", "在最后一个关键帧之后追加一帧空姿态。"))
				.OnClicked(FOnClicked::CreateSP(this, &SPoseKeyTimeline::OnAddClicked))
			]

			+ SHorizontalBox::Slot()
			.AutoWidth()
			[
				SNew(SButton)
				.Text(LOCTEXT("DeleteKey", "删除帧"))
				.ToolTipText(LOCTEXT("DeleteKeyTip", "删除当前选中的关键帧。"))
				.OnClicked(FOnClicked::CreateSP(this, &SPoseKeyTimeline::OnDeleteClicked))
			]
		]

		// 生成与播放：这是「参考姿态 + 求解轨迹 → 模型推理 → 视口播放」的入口。
		// 放在时间轴里而不是另开面板，是因为播放本身就是时间轴上的操作。
		+ SVerticalBox::Slot()
		.AutoHeight()
		.Padding(4.0f, 0.0f, 4.0f, 4.0f)
		[
			SNew(SHorizontalBox)

			+ SHorizontalBox::Slot()
			.AutoWidth()
			.Padding(0.0f, 0.0f, 4.0f, 0.0f)
			[
				SNew(SButton)
				.Text(LOCTEXT("GenerateAndPlay", "生成并播放"))
				.ToolTipText(LOCTEXT("GenerateAndPlayTip",
					"用姿态组求解出的根运动与关键帧参考姿态驱动模型，生成中间姿态并在视口播放。"))
				.OnClicked(FOnClicked::CreateSP(this, &SPoseKeyTimeline::OnGenerateClicked))
			]

			+ SHorizontalBox::Slot()
			.AutoWidth()
			.Padding(0.0f, 0.0f, 4.0f, 0.0f)
			[
				SNew(SButton)
				.Text(LOCTEXT("TogglePlayback", "暂停/继续"))
				.ToolTipText(LOCTEXT("TogglePlaybackTip", "暂停或继续播放已生成的姿态。"))
				.OnClicked(FOnClicked::CreateSP(this, &SPoseKeyTimeline::OnTogglePlaybackClicked))
			]

			+ SHorizontalBox::Slot()
			.AutoWidth()
			[
				SNew(SButton)
				.Text(LOCTEXT("StopPlayback", "停止"))
				.ToolTipText(LOCTEXT("StopPlaybackTip", "停止播放并回到第一帧。"))
				.OnClicked(FOnClicked::CreateSP(this, &SPoseKeyTimeline::OnStopPlaybackClicked))
			]
		]

		// 状态必须在这里可见：生成失败时若只在内部记一个字符串，
		// 作者会以为按钮没生效。这里同时承担「失败原因」与「播放进度」。
		+ SVerticalBox::Slot()
		.AutoHeight()
		.Padding(6.0f, 0.0f, 6.0f, 4.0f)
		[
			SNew(STextBlock)
			.Text(this, &SPoseKeyTimeline::GetPlaybackStatusText)
			.ColorAndOpacity(this, &SPoseKeyTimeline::GetPlaybackStatusColor)
			.AutoWrapText(true)
		]

		+ SVerticalBox::Slot()
		.FillHeight(1.0f)
		[
			SNew(SBox)
			.MinDesiredHeight(120.0f)
			[
				SAssignNew(ListView, SListView<FRowPtr>)
				.ListItemsSource(&Rows)
				.OnGenerateRow(this, &SPoseKeyTimeline::OnGenerateRow)
				.OnSelectionChanged(this, &SPoseKeyTimeline::OnSelectionChanged)
				.SelectionMode(ESelectionMode::Single)
			]
		]
	];

	RebuildRows();
}

void SPoseKeyTimeline::RefreshRows()
{
	RebuildRows();
}

void SPoseKeyTimeline::RebuildRows()
{
	Rows.Reset();

	const TWeakPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr;
	const UPoseGroupProxyAsset* Asset = Editor.IsValid() ? Editor.Pin()->GetAsset() : nullptr;
	if (Asset != nullptr)
	{
		Rows.Reserve(Asset->Keys.Num());
		for (int32 Index = 0; Index < Asset->Keys.Num(); ++Index)
		{
			const FPoseKey& Key = Asset->Keys[Index];

			FRowPtr Row = MakeShared<FPoseKeyRow, ESPMode::ThreadSafe>();
			Row->Index = Index;
			Row->Frame = Key.Frame;
			Row->Seconds = Asset->Settings.FrameRate > 0.0
				? static_cast<double>(Key.Frame) / Asset->Settings.FrameRate
				: 0.0;
			Row->LocalLocation = Key.LocalLocation;
			Row->PoseName = Key.PoseAsset != nullptr
				? Key.PoseAsset->GetName()
				: LOCTEXT("EmptyPose", "（空）").ToString();
			Rows.Add(Row);
		}
	}

	if (ListView.IsValid())
	{
		ListView->RequestListRefresh();
		// 行对象是新建的，SListView 会丢掉旧的选中指针；按编辑器当前下标重新选中，
		// 否则点完「生成并播放」后时间轴看起来像什么都没选。
		if (Editor.IsValid())
		{
			SetSelectedIndex(Editor.Pin()->GetSelectedKeyIndex());
		}
	}
}

void SPoseKeyTimeline::SetSelectedIndex(int32 InIndex)
{
	if (!ListView.IsValid())
	{
		return;
	}

	if (Rows.IsValidIndex(InIndex))
	{
		ListView->SetSelection(Rows[InIndex]);
	}
	else
	{
		ListView->ClearSelection();
	}
}

void SPoseKeyTimeline::OnSelectionChanged(FRowPtr Row, ESelectInfo::Type SelectInfo)
{
	// 程序化设置选中时不要反过来通知编辑器，否则会形成回环。
	if (SelectInfo == ESelectInfo::Direct || !Row.IsValid())
	{
		return;
	}

	if (const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin())
	{
		Editor->SetSelectedKeyIndex(Row->Index);
	}
}

TSharedRef<ITableRow> SPoseKeyTimeline::OnGenerateRow(
	FRowPtr Row, const TSharedRef<STableViewBase>& OwnerTable)
{
	FNumberFormattingOptions SecondsFormat;
	SecondsFormat.SetMinimumFractionalDigits(3);
	SecondsFormat.SetMaximumFractionalDigits(3);
	SecondsFormat.SetUseGrouping(false);

	return SNew(STableRow<FRowPtr>, OwnerTable)
		.Padding(2.0f)
		[
			SNew(SHorizontalBox)

			// 时间按秒编辑：帧号是内部单位，作者排节奏时想的是「第几秒」。
			// 帧号仍然显示在旁边，方便与求解器/日志对照。
			+ SHorizontalBox::Slot()
			.AutoWidth()
			.VAlign(VAlign_Center)
			.Padding(4.0f, 0.0f)
			[
				SNew(STextBlock).Text(LOCTEXT("SecondsLabel", "秒"))
			]

			+ SHorizontalBox::Slot()
			.AutoWidth()
			.VAlign(VAlign_Center)
			[
				SNew(SBox)
				.WidthOverride(64.0f)
				[
					SNew(SEditableTextBox)
					.Text(FText::AsNumber(Row->Seconds, &SecondsFormat))
					.OnTextCommitted(FOnTextCommitted::CreateSP(this, &SPoseKeyTimeline::OnSecondsCommitted, Row))
				]
			]

			+ SHorizontalBox::Slot()
			.AutoWidth()
			.VAlign(VAlign_Center)
			.Padding(2.0f, 0.0f)
			[
				SNew(STextBlock)
				.Text(FText::FromString(FString::Printf(TEXT("帧 %d"), Row->Frame)))
				.ColorAndOpacity(FSlateColor(FLinearColor(0.6f, 0.6f, 0.6f)))
			]

			+ SHorizontalBox::Slot()
			.FillWidth(1.0f)
			.VAlign(VAlign_Center)
			.Padding(8.0f, 0.0f)
			[
				SNew(STextBlock).Text(FText::FromString(Row->PoseName))
			]

			+ SHorizontalBox::Slot()
			.AutoWidth()
			.VAlign(VAlign_Center)
			.Padding(8.0f, 0.0f)
			[
				SNew(STextBlock).Text(FText::FromString(
					FString::Printf(TEXT("(%.0f, %.0f, %.0f)"),
						Row->LocalLocation.X, Row->LocalLocation.Y, Row->LocalLocation.Z)))
			]
		];
}

FReply SPoseKeyTimeline::OnAddClicked()
{
	if (const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin())
	{
		Editor->AddKey();
	}
	return FReply::Handled();
}

FReply SPoseKeyTimeline::OnGenerateClicked()
{
	if (const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin())
	{
		Editor->GenerateAndPlay();
		RebuildRows();
	}
	return FReply::Handled();
}

FReply SPoseKeyTimeline::OnTogglePlaybackClicked()
{
	if (const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin())
	{
		Editor->TogglePlayback();
	}
	return FReply::Handled();
}

FReply SPoseKeyTimeline::OnStopPlaybackClicked()
{
	if (const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin())
	{
		Editor->StopPlayback();
	}
	return FReply::Handled();
}

FText SPoseKeyTimeline::GetPlaybackStatusText() const
{
	const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin();
	if (!Editor.IsValid())
	{
		return FText::GetEmpty();
	}
	// 状态文字必须能说清「结果是否可用」，否则作者会拿过期缓存当当前结果。
	return FText::FromString(Editor->GetPlaybackStatusText());
}

FSlateColor SPoseKeyTimeline::GetPlaybackStatusColor() const
{
	const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin();
	if (!Editor.IsValid())
	{
		return FSlateColor(FLinearColor(0.7f, 0.7f, 0.7f));
	}
	if (Editor->HasGenerationError())
	{
		return FSlateColor(FLinearColor(1.0f, 0.35f, 0.3f));
	}
	if (Editor->IsGenerationStale())
	{
		return FSlateColor(FLinearColor(1.0f, 0.75f, 0.25f));
	}
	return FSlateColor(FLinearColor(0.55f, 0.85f, 1.0f));
}

FReply SPoseKeyTimeline::OnDeleteClicked()
{
	if (const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin())
	{
		Editor->DeleteSelectedKey();
	}
	return FReply::Handled();
}

void SPoseKeyTimeline::OnSecondsCommitted(
	const FText& NewText, ETextCommit::Type CommitType, FRowPtr Row)
{
	// 作者改完通常直接去点「生成并播放」，焦点离开输入框时也要提交；
	// 只认 OnEnter 会让这次改动静默丢失。
	if ((CommitType != ETextCommit::OnEnter && CommitType != ETextCommit::OnUserMovedFocus)
		|| !Row.IsValid())
	{
		return;
	}

	double NewSeconds = 0.0;
	if (!FDefaultValueHelper::ParseDouble(NewText.ToString(), NewSeconds)
		|| !FMath::IsFinite(NewSeconds) || NewSeconds < 0.0)
	{
		return;
	}
	if (const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin())
	{
		Editor->SetKeyTimeSeconds(Row->Index, NewSeconds);
		// 被拒绝时资产里的帧号没有变，输入框却还显示刚敲的值。
		// 重新生成行，让界面回到资产真值（接受时 RefreshFromAsset 也会做一次）。
		if (ListView.IsValid())
		{
			ListView->RequestListRefresh();
		}
	}
}

#undef LOCTEXT_NAMESPACE
