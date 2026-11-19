// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "RefPose/RefPoseGenerationSettings.h"
#include "Styling/SlateColor.h"
#include "Widgets/SCompoundWidget.h"
#include "Widgets/Input/SComboBox.h"
#include "Widgets/Views/SListView.h"

class FPoseGroupProxyAssetEditor;
class UPoseGroupProxyAsset;

/** 时间轴里的一行：一个关键帧。 */
struct FPoseKeyRow
{
	int32 Index = INDEX_NONE;

	/** 帧号。时间轴上按它排序，也是这一段运动的真实时间轴。 */
	int32 Frame = 0;

	/** 帧号换算出的秒数；编辑器按它展示与编辑。 */
	double Seconds = 0.0;

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

	/** 生成并播放；未生成或已过期时由编辑器自动生成。 */
	FReply OnGenerateClicked();
	FReply OnTogglePlaybackClicked();
	FReply OnStopPlaybackClicked();
	FReply OnPrevFrameClicked();
	FReply OnNextFrameClicked();
	FReply OnPrevKeyClicked();
	FReply OnNextKeyClicked();
	FReply OnPrevAnomalyClicked();
	FReply OnNextAnomalyClicked();
	FReply OnJumpToWorstFrameClicked();

	/** 播放进度条。 */
	float GetPlaybackSliderValue() const;
	void OnPlaybackSliderChanged(float Value);
	bool HasPlaybackFrames() const;
	FText GetPlaybackFrameText() const;

	/** 异常帧统计：多少帧被判为抽搐、当前帧是否异常。 */
	FText GetAnomalySummaryText() const;
	FSlateColor GetAnomalySummaryColor() const;

	/** 当前帧的「生成帧 → 求解帧 → 作者时间」调试行。 */
	FText GetPlaybackDebugText() const;

	/** 时间轴刻度提示：每个关键帧的秒数 + 硬参考节奏。 */
	FText GetKeyframeStripText() const;

	ECheckBoxState GetKeepKeyframesCheckState() const;
	void OnKeepKeyframesChanged(ECheckBoxState NewState);

	ECheckBoxState GetAuthorGhostCheckState() const;
	void OnAuthorGhostChanged(ECheckBoxState NewState);

	/** 播放状态文字：帧号 / 状态。 */
	FText GetPlaybackStatusText() const;

	/** 状态文字颜色：失败标红、过期标黄、播放中标蓝。 */
	FSlateColor GetPlaybackStatusColor() const;

	/** 选中行的秒数被人改过之后换算回帧号并写回资产。 */
	void OnSecondsCommitted(const FText& NewText, ETextCommit::Type CommitType, FRowPtr Row);

	//~ Begin 生成设置
	TSharedRef<SWidget> OnGenerateReferenceOption(
		TSharedPtr<ERefPoseReferencePattern> Item) const;
	void OnReferencePatternChanged(
		TSharedPtr<ERefPoseReferencePattern> Item, ESelectInfo::Type SelectInfo);
	FText GetSelectedReferencePatternText() const;
	TSharedPtr<ERefPoseReferencePattern> FindReferenceOption(
		ERefPoseReferencePattern Pattern) const;

	int32 GetStartKeyIndexForWidget() const;
	TOptional<int32> GetStartKeyIndexMax() const;
	void OnStartKeyCommitted(int32 Value, ETextCommit::Type CommitType);
	FText GetStartKeyTimeText() const;
	FReply OnStartFromSelectedClicked();

	FText GetPackagePathText() const;
	void OnPackagePathCommitted(const FText& NewText, ETextCommit::Type CommitType);
	FReply OnBrowsePackageClicked();

	ECheckBoxState GetUseGroupFrameRateCheckState() const;
	void OnUseGroupFrameRateChanged(ECheckBoxState NewState);

	ECheckBoxState GetTransferAuthoredTwistCheckState() const;
	void OnTransferAuthoredTwistChanged(ECheckBoxState NewState);

	FReply OnExportDiagnosticsClicked();
	FText GetDiagnosticsText() const;
	FText GetDiagnosticsExportStatusText() const;
	//~ End 生成设置

	TWeakPtr<FPoseGroupProxyAssetEditor> EditorPtr;

	TArray<FRowPtr> Rows;
	TSharedPtr<SListView<FRowPtr>> ListView;

	/** 参考档位下拉项；指向固定枚举值，生命周期与控件一致。 */
	TArray<TSharedPtr<ERefPoseReferencePattern>> ReferenceOptions;
	TSharedPtr<SComboBox<TSharedPtr<ERefPoseReferencePattern>>> ReferenceComboBox;

	/** 「导出诊断」最近一次结果，直接显示在按钮旁。 */
	FString DiagnosticsExportStatus;
};
