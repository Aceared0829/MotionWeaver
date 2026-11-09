// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "Widgets/SCompoundWidget.h"
#include "Widgets/Views/SListView.h"

class FPoseGroupProxyAssetEditor;
class UPoseGroupProxyAsset;

/** 时间轴里的一行：一个关键帧。 */
struct FPoseKeyRow
{
	int32 Index = INDEX_NONE;

	/** 帧号。时间轴上按它排序，也是这一段运动的真实时间轴。 */
	int32 Frame = 0;

	/** 这一帧用的姿态资产名，没有时显示「空」。 */
	FString PoseName;

	/** 相对起点的位置，给作者一个数字参照。 */
	FVector LocalLocation = FVector::ZeroVector;
};

/**
 * 姿态代理组的时间轴：列出所有关键帧，点击切帧，可增删、可改帧号。
 *
 * 为什么第一版用 SListView 而不是自绘的横向时间轴条：
 * 横向条要自己处理缩放、拖拽手柄、吸附、重叠避让，工作量是列表的十倍，
 * 而作者真正需要的先是「看得见有哪几帧、能点、能删、能改帧号」。
 * 等这些跑通了再换成条状也不迟——数据模型不会变。
 */
class SPoseKeyTimeline final : public SCompoundWidget
{
public:
	/** 列表项类型。显式写死模式，避免 TSharedPtr 默认模式与 SListView 推导不一致。 */
	using FRowPtr = TSharedPtr<FPoseKeyRow, ESPMode::ThreadSafe>;

	SLATE_BEGIN_ARGS(SPoseKeyTimeline) {}
		/** 宿主编辑器，增删改都通过它走，保证三方同步。 */
		SLATE_ARGUMENT(TWeakPtr<FPoseGroupProxyAssetEditor>, Editor)
	SLATE_END_ARGS()

	void Construct(const FArguments& InArgs);

	/** 资产内容变化后重建列表。 */
	void RefreshRows();

	/** 外部（视口）改了选中帧时同步高亮。 */
	void SetSelectedIndex(int32 InIndex);

private:
	/** 重建行数据。 */
	void RebuildRows();

	/** 选中某一行时通知编辑器。 */
	void OnSelectionChanged(FRowPtr Row, ESelectInfo::Type SelectInfo);

	TSharedRef<ITableRow> OnGenerateRow(FRowPtr Row, const TSharedRef<STableViewBase>& OwnerTable);

	FReply OnAddClicked();
	FReply OnDeleteClicked();

	/** 选中行的帧号被人改过之后写回资产。 */
	void OnFrameCommitted(const FText& NewText, ETextCommit::Type CommitType, FRowPtr Row);

	TWeakPtr<FPoseGroupProxyAssetEditor> EditorPtr;

	TArray<FRowPtr> Rows;
	TSharedPtr<SListView<FRowPtr>> ListView;
};
