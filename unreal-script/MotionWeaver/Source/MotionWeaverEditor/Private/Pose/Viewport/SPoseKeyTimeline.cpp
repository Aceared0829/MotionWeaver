// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Viewport/SPoseKeyTimeline.h"

#include "Pose/Assets/Editors/PoseGroupProxyAssetEditor.h"
#include "Pose/Data/PoseGroupProxyAsset.h"
#include "Pose/Data/PoseProxyAsset.h"

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
	return SNew(STableRow<FRowPtr>, OwnerTable)
		.Padding(2.0f)
		[
			SNew(SHorizontalBox)

			// 帧号可直接编辑：跑酷的蓄力段与腾空段节奏不同，
			// 改帧号是最常见的调整，不该逼用户回细节面板。
			+ SHorizontalBox::Slot()
			.AutoWidth()
			.VAlign(VAlign_Center)
			.Padding(4.0f, 0.0f)
			[
				SNew(STextBlock).Text(LOCTEXT("FrameLabel", "帧"))
			]

			+ SHorizontalBox::Slot()
			.AutoWidth()
			.VAlign(VAlign_Center)
			[
				SNew(SBox)
				.WidthOverride(64.0f)
				[
					SNew(SEditableTextBox)
					.Text(FText::AsNumber(Row->Frame))
					.OnTextCommitted(FOnTextCommitted::CreateSP(this, &SPoseKeyTimeline::OnFrameCommitted, Row))
				]
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

FReply SPoseKeyTimeline::OnDeleteClicked()
{
	if (const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin())
	{
		Editor->DeleteSelectedKey();
	}
	return FReply::Handled();
}

void SPoseKeyTimeline::OnFrameCommitted(
	const FText& NewText, ETextCommit::Type CommitType, FRowPtr Row)
{
	if (CommitType != ETextCommit::OnEnter || !Row.IsValid())
	{
		return;
	}

	const int32 NewFrame = FCString::Atoi(*NewText.ToString());
	if (const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin())
	{
		Editor->SetSelectedKeyFrame(NewFrame);
	}
}

#undef LOCTEXT_NAMESPACE
