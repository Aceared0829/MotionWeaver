// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Viewport/SPoseKeyTimeline.h"

#include "Pose/Assets/Editors/PoseGroupProxyAssetEditor.h"
#include "Pose/Data/PoseGroupProxyAsset.h"
#include "Pose/Data/PoseProxyAsset.h"

#include "DesktopPlatformModule.h"
#include "Misc/Paths.h"
#include "Misc/DefaultValueHelper.h"
#include "Styling/AppStyle.h"
#include "Styling/CoreStyle.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Input/SCheckBox.h"
#include "Widgets/Input/SEditableTextBox.h"
#include "Widgets/Input/SSlider.h"
#include "Widgets/Input/SSpinBox.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/Layout/SExpandableArea.h"
#include "Widgets/Layout/SScrollBox.h"
#include "Widgets/Layout/SSeparator.h"
#include "Widgets/Layout/SSpacer.h"
#include "Widgets/SBoxPanel.h"
#include "Widgets/Text/STextBlock.h"

#define LOCTEXT_NAMESPACE "SPoseKeyTimeline"

void SPoseKeyTimeline::Construct(const FArguments& InArgs)
{
	EditorPtr = InArgs._Editor;

	// 只开放训练验证过的四档；顺序从「约束最少」到「最完整」，便于作者理解。
	ReferenceOptions = {
		MakeShared<ERefPoseReferencePattern>(ERefPoseReferencePattern::None),
		MakeShared<ERefPoseReferencePattern>(ERefPoseReferencePattern::Middle),
		MakeShared<ERefPoseReferencePattern>(ERefPoseReferencePattern::End),
		MakeShared<ERefPoseReferencePattern>(ERefPoseReferencePattern::MiddleAndEnd),
	};

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

		// 生成与播放：把「参考姿态 + 求解轨迹 → 模型推理 → 视口播放 + 逐帧检查」
		// 收在一块面板里。布局顺序按调试动线排：先生成、再走帧、再看数值、
		// 最后才是参数——参数改一次就要重跑，不该挡在最前面。
		+ SVerticalBox::Slot()
		.AutoHeight()
		.Padding(4.0f, 0.0f, 4.0f, 4.0f)
		[
			SNew(SBorder)
			.BorderImage(FAppStyle::GetBrush("ToolPanel.GroupBorder"))
			.Padding(8.0f)
			[
				SNew(SVerticalBox)

				// 标题行：左侧标题，右侧异常徽标。异常徽标常驻，
				// 让人一眼知道「这段结果值不值得逐帧看」。
				+ SVerticalBox::Slot()
				.AutoHeight()
				[
					SNew(SHorizontalBox)

					+ SHorizontalBox::Slot()
					.AutoWidth()
					.VAlign(VAlign_Center)
					[
						SNew(STextBlock)
						.Text(LOCTEXT("GenerationSection", "生成与播放"))
						.Font(FCoreStyle::GetDefaultFontStyle("Bold", 10))
						.ColorAndOpacity(FSlateColor(FLinearColor(0.92f, 0.92f, 0.92f)))
					]

					+ SHorizontalBox::Slot()
					.FillWidth(1.0f)
					[
						SNew(SSpacer)
					]

					+ SHorizontalBox::Slot()
					.AutoWidth()
					.VAlign(VAlign_Center)
					[
						SNew(STextBlock)
						.Text(this, &SPoseKeyTimeline::GetAnomalySummaryText)
						.ColorAndOpacity(this, &SPoseKeyTimeline::GetAnomalySummaryColor)
						.Font(FCoreStyle::GetDefaultFontStyle("Bold", 8))
					]
				]

				// 主操作：生成是唯一会跑推理的入口，因此放最左、加重。
				+ SVerticalBox::Slot()
				.AutoHeight()
				.Padding(0.0f, 6.0f, 0.0f, 0.0f)
				[
					SNew(SHorizontalBox)

					+ SHorizontalBox::Slot()
					.AutoWidth()
					.Padding(0.0f, 0.0f, 4.0f, 0.0f)
					[
						SNew(SButton)
						.Text(LOCTEXT("GenerateAndPlay", "▶  生成并播放"))
						.ToolTipText(LOCTEXT("GenerateAndPlayTip",
							"用姿态组求解出的根运动与关键帧参考姿态驱动模型，生成中间姿态并在视口播放。"))
						.OnClicked(FOnClicked::CreateSP(this, &SPoseKeyTimeline::OnGenerateClicked))
					]

					+ SHorizontalBox::Slot()
					.AutoWidth()
					.Padding(0.0f, 0.0f, 4.0f, 0.0f)
					[
						SNew(SButton)
						.Text(LOCTEXT("TogglePlayback", "‖  暂停/继续"))
						.ToolTipText(LOCTEXT("TogglePlaybackTip", "暂停或继续播放已生成的姿态。"))
						.OnClicked(FOnClicked::CreateSP(this, &SPoseKeyTimeline::OnTogglePlaybackClicked))
					]

					+ SHorizontalBox::Slot()
					.AutoWidth()
					[
						SNew(SButton)
						.Text(LOCTEXT("StopPlayback", "■  停止"))
						.ToolTipText(LOCTEXT("StopPlaybackTip", "停止播放并回到第一帧。"))
						.OnClicked(FOnClicked::CreateSP(this, &SPoseKeyTimeline::OnStopPlaybackClicked))
					]
				]

				// 定位工具：逐帧 / 逐关键帧 / 逐异常帧。三者是不同粒度，
				// 分开摆放，避免「想找异常却按成了逐帧」。
				+ SVerticalBox::Slot()
				.AutoHeight()
				.Padding(0.0f, 4.0f, 0.0f, 0.0f)
				[
					SNew(SHorizontalBox)

					+ SHorizontalBox::Slot()
					.AutoWidth()
					.VAlign(VAlign_Center)
					.Padding(0.0f, 0.0f, 4.0f, 0.0f)
					[
						SNew(STextBlock)
						.Text(LOCTEXT("NavFrame", "逐帧"))
						.ColorAndOpacity(FSlateColor(FLinearColor(0.6f, 0.6f, 0.6f)))
					]

					+ SHorizontalBox::Slot()
					.AutoWidth()
					.Padding(0.0f, 0.0f, 2.0f, 0.0f)
					[
						SNew(SButton)
						.Text(LOCTEXT("PrevFrame", "◀"))
						.ToolTipText(LOCTEXT("PrevFrameTip", "后退一帧（并暂停）。"))
						.OnClicked(FOnClicked::CreateSP(this, &SPoseKeyTimeline::OnPrevFrameClicked))
					]

					+ SHorizontalBox::Slot()
					.AutoWidth()
					.Padding(0.0f, 0.0f, 12.0f, 0.0f)
					[
						SNew(SButton)
						.Text(LOCTEXT("NextFrame", "▶"))
						.ToolTipText(LOCTEXT("NextFrameTip", "前进一帧（并暂停）。"))
						.OnClicked(FOnClicked::CreateSP(this, &SPoseKeyTimeline::OnNextFrameClicked))
					]

					+ SHorizontalBox::Slot()
					.AutoWidth()
					.VAlign(VAlign_Center)
					.Padding(0.0f, 0.0f, 4.0f, 0.0f)
					[
						SNew(STextBlock)
						.Text(LOCTEXT("NavKey", "关键帧"))
						.ColorAndOpacity(FSlateColor(FLinearColor(0.6f, 0.6f, 0.6f)))
					]

					+ SHorizontalBox::Slot()
					.AutoWidth()
					.Padding(0.0f, 0.0f, 2.0f, 0.0f)
					[
						SNew(SButton)
						.Text(LOCTEXT("PrevKey", "◀"))
						.ToolTipText(LOCTEXT("PrevKeyTip",
							"跳到上一个作者关键帧对应的生成帧，便于对照参考姿态。"))
						.OnClicked(FOnClicked::CreateSP(this, &SPoseKeyTimeline::OnPrevKeyClicked))
					]

					+ SHorizontalBox::Slot()
					.AutoWidth()
					.Padding(0.0f, 0.0f, 12.0f, 0.0f)
					[
						SNew(SButton)
						.Text(LOCTEXT("NextKey", "▶"))
						.ToolTipText(LOCTEXT("NextKeyTip",
							"跳到下一个作者关键帧对应的生成帧，便于对照参考姿态。"))
						.OnClicked(FOnClicked::CreateSP(this, &SPoseKeyTimeline::OnNextKeyClicked))
					]

					+ SHorizontalBox::Slot()
					.AutoWidth()
					.VAlign(VAlign_Center)
					.Padding(0.0f, 0.0f, 4.0f, 0.0f)
					[
						SNew(STextBlock)
						.Text(LOCTEXT("NavAnomaly", "异常帧"))
						.ColorAndOpacity(FSlateColor(FLinearColor(1.0f, 0.72f, 0.3f)))
					]

					+ SHorizontalBox::Slot()
					.AutoWidth()
					.Padding(0.0f, 0.0f, 2.0f, 0.0f)
					[
						SNew(SButton)
						.Text(LOCTEXT("PrevAnomaly", "◀"))
						.ToolTipText(LOCTEXT("PrevAnomalyTip",
							"跳到上一个位置跳变 >5cm 或旋转跳变 >45° 的帧；没有时会提示而不是静默不动。"))
						.OnClicked(FOnClicked::CreateSP(this, &SPoseKeyTimeline::OnPrevAnomalyClicked))
					]

					+ SHorizontalBox::Slot()
					.AutoWidth()
					.Padding(0.0f, 0.0f, 6.0f, 0.0f)
					[
						SNew(SButton)
						.Text(LOCTEXT("NextAnomaly", "▶"))
						.ToolTipText(LOCTEXT("NextAnomalyTip",
							"跳到下一个位置跳变 >5cm 或旋转跳变 >45° 的帧；没有时会提示而不是静默不动。"))
						.OnClicked(FOnClicked::CreateSP(this, &SPoseKeyTimeline::OnNextAnomalyClicked))
					]

					+ SHorizontalBox::Slot()
					.AutoWidth()
					[
						SNew(SButton)
						.Text(LOCTEXT("JumpWorst", "跳到最差帧"))
						.ToolTipText(LOCTEXT("JumpWorstTip",
							"按「超过阈值多少倍」归一后挑最严重的一帧，直接拖到那里看姿态。"))
						.OnClicked(FOnClicked::CreateSP(this, &SPoseKeyTimeline::OnJumpToWorstFrameClicked))
					]
				]

				// 进度条 + 帧号：滑杆停靠后显示「当前/总帧 + 秒数」。
				+ SVerticalBox::Slot()
				.AutoHeight()
				.Padding(0.0f, 6.0f, 0.0f, 0.0f)
				[
					SNew(SHorizontalBox)

					+ SHorizontalBox::Slot()
					.FillWidth(1.0f)
					.VAlign(VAlign_Center)
					[
						SNew(SSlider)
						.Value(this, &SPoseKeyTimeline::GetPlaybackSliderValue)
						.OnValueChanged(this, &SPoseKeyTimeline::OnPlaybackSliderChanged)
						.IsEnabled(this, &SPoseKeyTimeline::HasPlaybackFrames)
						.ToolTipText(LOCTEXT("PlaybackSliderTip",
							"拖动逐帧检查；拖动会自动暂停。"))
					]

					+ SHorizontalBox::Slot()
					.AutoWidth()
					.VAlign(VAlign_Center)
					.Padding(8.0f, 0.0f, 0.0f, 0.0f)
					[
						SNew(SBox)
						.WidthOverride(128.0f)
						[
							SNew(STextBlock)
							.Text(this, &SPoseKeyTimeline::GetPlaybackFrameText)
							.Font(FCoreStyle::GetDefaultFontStyle("Mono", 8))
							.ColorAndOpacity(FSlateColor(FLinearColor(0.75f, 0.88f, 1.0f)))
						]
					]
				]

				// 时间轴刻度提示：作者时间 → 关键帧 → 硬参考节奏。
				+ SVerticalBox::Slot()
				.AutoHeight()
				.Padding(0.0f, 2.0f, 0.0f, 0.0f)
				[
					SNew(STextBlock)
					.Text(this, &SPoseKeyTimeline::GetKeyframeStripText)
					.Font(FCoreStyle::GetDefaultFontStyle("Mono", 7))
					.ColorAndOpacity(FSlateColor(FLinearColor(0.55f, 0.55f, 0.55f)))
					.AutoWrapText(true)
				]

				// 当前帧四元信息：生成帧 / 求解帧 / 作者时间 / 窗口 / 硬参考。
				+ SVerticalBox::Slot()
				.AutoHeight()
				.Padding(0.0f, 2.0f, 0.0f, 0.0f)
				[
					SNew(STextBlock)
					.Text(this, &SPoseKeyTimeline::GetPlaybackDebugText)
					.ColorAndOpacity(FSlateColor(FLinearColor(0.62f, 0.84f, 1.0f)))
					.Font(FCoreStyle::GetDefaultFontStyle("Mono", 8))
					.AutoWrapText(true)
				]

				// 状态必须在这里可见：生成失败时若只在内部记一个字符串，
				// 作者会以为按钮没生效。这里同时承担「失败原因」与「播放进度」。
				+ SVerticalBox::Slot()
				.AutoHeight()
				.Padding(0.0f, 2.0f, 0.0f, 0.0f)
				[
					SNew(STextBlock)
					.Text(this, &SPoseKeyTimeline::GetPlaybackStatusText)
					.ColorAndOpacity(this, &SPoseKeyTimeline::GetPlaybackStatusColor)
					.AutoWrapText(true)
				]

				+ SVerticalBox::Slot()
				.AutoHeight()
				.Padding(0.0f, 6.0f, 0.0f, 6.0f)
				[
					SNew(SSeparator)
				]

				// 生成设置：存在资产上，重开编辑器仍按同一组参数复现。
				// 参考档位只有训练验证过的四档；任意帧手选属于未验证档位，不放进 UI。
				+ SVerticalBox::Slot()
				.AutoHeight()
				[
					SNew(SHorizontalBox)

					+ SHorizontalBox::Slot()
					.AutoWidth()
					.VAlign(VAlign_Center)
					[
						SNew(STextBlock)
						.Text(LOCTEXT("SettingsSection", "生成设置"))
						.Font(FCoreStyle::GetDefaultFontStyle("Bold", 8))
						.ColorAndOpacity(FSlateColor(FLinearColor(0.7f, 0.7f, 0.7f)))
					]
				]

				+ SVerticalBox::Slot()
				.AutoHeight()
				.Padding(0.0f, 4.0f, 0.0f, 0.0f)
				[
					SNew(SHorizontalBox)

					+ SHorizontalBox::Slot()
					.AutoWidth()
					.VAlign(VAlign_Center)
					[
						SNew(SBox)
						.WidthOverride(52.0f)
						[
							SNew(STextBlock).Text(LOCTEXT("ReferencePatternLabel", "参考姿态"))
						]
					]

					+ SHorizontalBox::Slot()
					.AutoWidth()
					.VAlign(VAlign_Center)
					.Padding(0.0f, 0.0f, 14.0f, 0.0f)
					[
						SAssignNew(ReferenceComboBox, SComboBox<TSharedPtr<ERefPoseReferencePattern>>)
						.OptionsSource(&ReferenceOptions)
						.InitiallySelectedItem(FindReferenceOption(
							EditorPtr.IsValid()
								? EditorPtr.Pin()->GetReferencePattern()
								: ERefPoseReferencePattern::MiddleAndEnd))
						.OnGenerateWidget(this, &SPoseKeyTimeline::OnGenerateReferenceOption)
						.OnSelectionChanged(this, &SPoseKeyTimeline::OnReferencePatternChanged)
						.ToolTipText(LOCTEXT("ReferencePatternTip",
							"窗口内哪几帧作为硬参考。只有训练验证过的四档可选。"))
						[
							SNew(STextBlock)
							.Text(this, &SPoseKeyTimeline::GetSelectedReferencePatternText)
						]
					]

					+ SHorizontalBox::Slot()
					.AutoWidth()
					.VAlign(VAlign_Center)
					.Padding(0.0f, 0.0f, 4.0f, 0.0f)
					[
						SNew(STextBlock).Text(LOCTEXT("StartKeyLabel", "起点"))
					]

					+ SHorizontalBox::Slot()
					.AutoWidth()
					.VAlign(VAlign_Center)
					[
						SNew(SBox)
						.WidthOverride(56.0f)
						[
							SNew(SSpinBox<int32>)
							.MinValue(TOptional<int32>(0))
							.MaxValue(this, &SPoseKeyTimeline::GetStartKeyIndexMax)
							.Value(this, &SPoseKeyTimeline::GetStartKeyIndexForWidget)
							.OnValueCommitted(this, &SPoseKeyTimeline::OnStartKeyCommitted)
							.ToolTipText(LOCTEXT("StartKeyTip",
								"从第几个关键帧开始生成。索引 >0 时该帧之前必须有至少 24 帧求解样本，否则会明确报错。"))
						]
					]

					+ SHorizontalBox::Slot()
					.AutoWidth()
					.VAlign(VAlign_Center)
					.Padding(6.0f, 0.0f, 8.0f, 0.0f)
					[
						SNew(STextBlock)
						.Text(this, &SPoseKeyTimeline::GetStartKeyTimeText)
						.ColorAndOpacity(FSlateColor(FLinearColor(0.6f, 0.6f, 0.6f)))
						.Font(FCoreStyle::GetDefaultFontStyle("Mono", 7))
					]

					+ SHorizontalBox::Slot()
					.AutoWidth()
					.VAlign(VAlign_Center)
					[
						SNew(SButton)
						.Text(LOCTEXT("StartFromSelected", "用选中帧"))
						.ToolTipText(LOCTEXT("StartFromSelectedTip",
							"把生成起点设为时间轴上当前选中的关键帧。"))
						.OnClicked(FOnClicked::CreateSP(this, &SPoseKeyTimeline::OnStartFromSelectedClicked))
					]
				]

				// 模型包路径：填了就以它为准，不存在时直接报错，不静默回退。
				+ SVerticalBox::Slot()
				.AutoHeight()
				.Padding(0.0f, 4.0f, 0.0f, 0.0f)
				[
					SNew(SHorizontalBox)

					+ SHorizontalBox::Slot()
					.AutoWidth()
					.VAlign(VAlign_Center)
					[
						SNew(SBox)
						.WidthOverride(52.0f)
						[
							SNew(STextBlock).Text(LOCTEXT("ModelPackageLabel", "模型包"))
						]
					]

					+ SHorizontalBox::Slot()
					.FillWidth(1.0f)
					.VAlign(VAlign_Center)
					[
						SNew(SEditableTextBox)
						.Text(this, &SPoseKeyTimeline::GetPackagePathText)
						.OnTextCommitted(this, &SPoseKeyTimeline::OnPackagePathCommitted)
						.HintText(LOCTEXT("ModelPackageHint",
							"留空则用 MW_REFPOSE_UE_PACKAGE 或 Saved/MotionWeaver/RefPosePackage.txt"))
					]

					+ SHorizontalBox::Slot()
					.AutoWidth()
					.VAlign(VAlign_Center)
					.Padding(4.0f, 0.0f, 0.0f, 0.0f)
					[
						SNew(SButton)
						.Text(LOCTEXT("Browse", "浏览…"))
						.OnClicked(FOnClicked::CreateSP(this, &SPoseKeyTimeline::OnBrowsePackageClicked))
					]
				]

				// 开关：按行分组，勾选项的说明放在 Tooltip 里，避免挤占时间轴。
				+ SVerticalBox::Slot()
				.AutoHeight()
				.Padding(0.0f, 6.0f, 0.0f, 0.0f)
				[
					SNew(SHorizontalBox)

					+ SHorizontalBox::Slot()
					.AutoWidth()
					.VAlign(VAlign_Center)
					[
						SNew(SCheckBox)
						.IsChecked(this, &SPoseKeyTimeline::GetUseGroupFrameRateCheckState)
						.OnCheckStateChanged(this, &SPoseKeyTimeline::OnUseGroupFrameRateChanged)
						.ToolTipText(LOCTEXT("UseGroupFrameRateTip",
							"默认按模型契约帧率（120 fps）播放；勾选后改用姿态组帧率，动作速度会变。"))
						[
							SNew(STextBlock).Text(LOCTEXT("UseGroupFrameRate", "按姿态组帧率"))
						]
					]

					+ SHorizontalBox::Slot()
					.AutoWidth()
					.VAlign(VAlign_Center)
					.Padding(14.0f, 0.0f, 0.0f, 0.0f)
					[
						SNew(SCheckBox)
						.IsChecked(this, &SPoseKeyTimeline::GetKeepKeyframesCheckState)
						.OnCheckStateChanged(this, &SPoseKeyTimeline::OnKeepKeyframesChanged)
						.ToolTipText(LOCTEXT("KeepKeyframesTip",
							"播放时继续显示关键帧幽灵，方便直接对照生成姿态。"))
						[
							SNew(STextBlock).Text(LOCTEXT("KeepKeyframes", "保留关键帧幽灵"))
						]
					]

					+ SHorizontalBox::Slot()
					.AutoWidth()
					.VAlign(VAlign_Center)
					.Padding(14.0f, 0.0f, 0.0f, 0.0f)
					[
						SNew(SCheckBox)
						.IsChecked(this, &SPoseKeyTimeline::GetAuthorGhostCheckState)
						.OnCheckStateChanged(this, &SPoseKeyTimeline::OnAuthorGhostChanged)
						.ToolTipText(LOCTEXT("AuthorGhostTip",
							"线框显示同一帧的关键帧插值姿态（模型实际看到的参考分布）。"
							"实体与线框的偏离量就是模型误差——用来判断畸形来自模型还是解算。"))
						[
							SNew(STextBlock).Text(LOCTEXT("AuthorGhost", "参考幽灵"))
						]
					]

					+ SHorizontalBox::Slot()
					.AutoWidth()
					.VAlign(VAlign_Center)
					.Padding(14.0f, 0.0f, 0.0f, 0.0f)
					[
						SNew(SCheckBox)
						.IsChecked(this, &SPoseKeyTimeline::GetTransferAuthoredTwistCheckState)
						.OnCheckStateChanged(this, &SPoseKeyTimeline::OnTransferAuthoredTwistChanged)
						.ToolTipText(LOCTEXT("TransferTwistTip",
							"模型只输出位置，绕骨轴的自转在位置上不可见（前臂旋前、手腕翻转、头朝向）。"
							"开启后用作者关键帧的 roll 补这份欠定信息：骨轴与所有关节位置仍来自模型，只改蒙皮的扭转。"
							"关闭可对照排查「畸形是位置错了还是 roll 丢了」。"))
						[
							SNew(STextBlock).Text(LOCTEXT("TransferTwist", "迁移扭转"))
						]
					]
				]

				+ SVerticalBox::Slot()
				.AutoHeight()
				.Padding(0.0f, 6.0f, 0.0f, 0.0f)
				[
					SNew(SHorizontalBox)

					+ SHorizontalBox::Slot()
					.AutoWidth()
					.VAlign(VAlign_Center)
					[
						SNew(SButton)
						.Text(LOCTEXT("ExportDiagnostics", "导出诊断 JSON"))
						.ToolTipText(LOCTEXT("ExportDiagnosticsTip",
							"把窗口耗时、接缝、逐帧跳变、锚点对齐误差与告警写到 Saved/MotionWeaver/RefPoseReports。"))
						.OnClicked(FOnClicked::CreateSP(this, &SPoseKeyTimeline::OnExportDiagnosticsClicked))
					]

					+ SHorizontalBox::Slot()
					.FillWidth(1.0f)
					.VAlign(VAlign_Center)
					.Padding(8.0f, 0.0f, 0.0f, 0.0f)
					[
						SNew(STextBlock)
						.Text(this, &SPoseKeyTimeline::GetDiagnosticsExportStatusText)
						.ColorAndOpacity(FSlateColor(FLinearColor(0.6f, 0.6f, 0.6f)))
						.AutoWrapText(true)
					]
				]
			]
		]

		// 列表表头：与行内布局大致对齐，扫一眼就知道哪列是什么。
		+ SVerticalBox::Slot()
		.AutoHeight()
		.Padding(8.0f, 2.0f, 8.0f, 2.0f)
		[
			SNew(SHorizontalBox)

			+ SHorizontalBox::Slot()
			.AutoWidth()
			[
				SNew(SBox)
				.WidthOverride(100.0f)
				[
					SNew(STextBlock)
					.Text(LOCTEXT("HeaderSeconds", "秒"))
					.Font(FCoreStyle::GetDefaultFontStyle("Bold", 8))
					.ColorAndOpacity(FSlateColor(FLinearColor(0.65f, 0.65f, 0.65f)))
				]
			]

			+ SHorizontalBox::Slot()
			.AutoWidth()
			[
				SNew(SBox)
				.WidthOverride(70.0f)
				[
					SNew(STextBlock)
					.Text(LOCTEXT("HeaderFrame", "帧"))
					.Font(FCoreStyle::GetDefaultFontStyle("Bold", 8))
					.ColorAndOpacity(FSlateColor(FLinearColor(0.65f, 0.65f, 0.65f)))
				]
			]

			+ SHorizontalBox::Slot()
			.FillWidth(1.0f)
			.Padding(8.0f, 0.0f)
			[
				SNew(STextBlock)
				.Text(LOCTEXT("HeaderPose", "姿态"))
				.Font(FCoreStyle::GetDefaultFontStyle("Bold", 8))
				.ColorAndOpacity(FSlateColor(FLinearColor(0.65f, 0.65f, 0.65f)))
			]

			+ SHorizontalBox::Slot()
			.AutoWidth()
			.Padding(8.0f, 0.0f)
			[
				SNew(STextBlock)
				.Text(LOCTEXT("HeaderLocation", "位置（cm）"))
				.Font(FCoreStyle::GetDefaultFontStyle("Bold", 8))
				.ColorAndOpacity(FSlateColor(FLinearColor(0.65f, 0.65f, 0.65f)))
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

		// 诊断默认折叠：不挤占时间轴，但生成后能立刻展开核对数值。
		+ SVerticalBox::Slot()
		.AutoHeight()
		.Padding(4.0f)
		[
			SNew(SExpandableArea)
			.InitiallyCollapsed(true)
			.AreaTitle(LOCTEXT("DiagnosticsTitle",
				"生成诊断（逐窗口耗时 / Root 接缝 / 旋转跳变 / 锚点误差）"))
			.BodyContent()
			[
				SNew(SBox)
				.MaxDesiredHeight(220.0f)
				[
					SNew(SScrollBox)

					+ SScrollBox::Slot()
					[
						SNew(STextBlock)
						.Text(this, &SPoseKeyTimeline::GetDiagnosticsText)
						.Font(FCoreStyle::GetDefaultFontStyle("Mono", 8))
						.AutoWrapText(true)
					]
				]
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

	// 细节面板也可能改参考档位；下拉框没有绑定属性，只能在这里同步。
	// SetSelectedItem 会以 Direct 触发回调，回调里要忽略这种程序化选择。
	if (ReferenceComboBox.IsValid())
	{
		ReferenceComboBox->SetSelectedItem(FindReferenceOption(
			Editor.IsValid()
				? Editor.Pin()->GetReferencePattern()
				: ERefPoseReferencePattern::MiddleAndEnd));
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

TSharedPtr<ERefPoseReferencePattern> SPoseKeyTimeline::FindReferenceOption(
	ERefPoseReferencePattern Pattern) const
{
	for (const TSharedPtr<ERefPoseReferencePattern>& Option : ReferenceOptions)
	{
		if (Option.IsValid() && *Option == Pattern)
		{
			return Option;
		}
	}
	return ReferenceOptions.Num() > 0
		? ReferenceOptions.Last()
		: TSharedPtr<ERefPoseReferencePattern>();
}

TSharedRef<SWidget> SPoseKeyTimeline::OnGenerateReferenceOption(
	TSharedPtr<ERefPoseReferencePattern> Item) const
{
	FText Label = LOCTEXT("ReferencePatternUnknown", "<未知>");
	if (Item.IsValid())
	{
		switch (*Item)
		{
		case ERefPoseReferencePattern::None:
			Label = LOCTEXT("ReferenceNone", "无参考");
			break;
		case ERefPoseReferencePattern::Middle:
			Label = LOCTEXT("ReferenceMiddle", "中间 (12)");
			break;
		case ERefPoseReferencePattern::End:
			Label = LOCTEXT("ReferenceEnd", "末端 (23)");
			break;
		case ERefPoseReferencePattern::MiddleAndEnd:
			Label = LOCTEXT("ReferenceMiddleAndEnd", "中间+末端 (12,23)");
			break;
		}
	}
	return SNew(STextBlock).Text(Label);
}

void SPoseKeyTimeline::OnReferencePatternChanged(
	TSharedPtr<ERefPoseReferencePattern> Item, ESelectInfo::Type SelectInfo)
{
	// 程序化同步（RebuildRows 里的 SetSelectedItem）也会走到这里，
	// 忽略它避免「刷新行 → 写资产 → 标脏」的回环。
	if (SelectInfo == ESelectInfo::Direct || !Item.IsValid())
	{
		return;
	}
	if (const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin())
	{
		Editor->SetReferencePattern(*Item);
	}
}

FText SPoseKeyTimeline::GetSelectedReferencePatternText() const
{
	const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin();
	if (!Editor.IsValid())
	{
		return FText::GetEmpty();
	}
	switch (Editor->GetReferencePattern())
	{
	case ERefPoseReferencePattern::None:
		return LOCTEXT("ReferenceNone", "无参考");
	case ERefPoseReferencePattern::Middle:
		return LOCTEXT("ReferenceMiddle", "中间 (12)");
	case ERefPoseReferencePattern::End:
		return LOCTEXT("ReferenceEnd", "末端 (23)");
	case ERefPoseReferencePattern::MiddleAndEnd:
	default:
		return LOCTEXT("ReferenceMiddleAndEnd", "中间+末端 (12,23)");
	}
}

int32 SPoseKeyTimeline::GetStartKeyIndexForWidget() const
{
	const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin();
	if (!Editor.IsValid())
	{
		return 0;
	}
	const UPoseGroupProxyAsset* Asset = Editor->GetAsset();
	const int32 MaxIndex = Asset != nullptr
		? FMath::Max(0, Asset->Keys.Num() - 1)
		: 0;
	return FMath::Clamp(Editor->GetStartKeyIndex(), 0, MaxIndex);
}

TOptional<int32> SPoseKeyTimeline::GetStartKeyIndexMax() const
{
	const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin();
	const UPoseGroupProxyAsset* Asset = Editor.IsValid() ? Editor->GetAsset() : nullptr;
	return TOptional<int32>(
		Asset != nullptr ? FMath::Max(0, Asset->Keys.Num() - 1) : 0);
}

void SPoseKeyTimeline::OnStartKeyCommitted(int32 Value, ETextCommit::Type CommitType)
{
	if (CommitType != ETextCommit::OnEnter
		&& CommitType != ETextCommit::OnUserMovedFocus)
	{
		return;
	}
	if (const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin())
	{
		Editor->SetStartKeyIndex(Value);
	}
}

FText SPoseKeyTimeline::GetStartKeyTimeText() const
{
	const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin();
	if (!Editor.IsValid())
	{
		return FText::GetEmpty();
	}
	return FText::FromString(FString::Printf(
		TEXT("（%.3f s）"), Editor->GetStartKeyTimeSeconds()));
}

FReply SPoseKeyTimeline::OnStartFromSelectedClicked()
{
	if (const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin())
	{
		const int32 SelectedIndex = Editor->GetSelectedKeyIndex();
		if (SelectedIndex >= 0)
		{
			Editor->SetStartKeyIndex(SelectedIndex);
		}
	}
	return FReply::Handled();
}

FText SPoseKeyTimeline::GetPackagePathText() const
{
	const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin();
	return Editor.IsValid()
		? FText::FromString(Editor->GetModelPackagePath())
		: FText::GetEmpty();
}

void SPoseKeyTimeline::OnPackagePathCommitted(
	const FText& NewText, ETextCommit::Type CommitType)
{
	// 与秒数输入一致：焦点离开也要提交，否则改完直接点「生成并播放」会丢改动。
	if (CommitType != ETextCommit::OnEnter
		&& CommitType != ETextCommit::OnUserMovedFocus)
	{
		return;
	}
	if (const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin())
	{
		Editor->SetModelPackagePath(NewText.ToString());
	}
}

FReply SPoseKeyTimeline::OnBrowsePackageClicked()
{
	if (const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin())
	{
		FString Current = Editor->GetModelPackagePath();
		if (Current.IsEmpty())
		{
			Current = FPaths::ProjectSavedDir();
		}
		FString Chosen;
		if (IDesktopPlatform* DesktopPlatform = FDesktopPlatformModule::Get())
		{
			if (DesktopPlatform->OpenDirectoryDialog(
				nullptr,
				LOCTEXT("ChooseModelPackage", "选择 RefPose 模型包目录").ToString(),
				Current,
				Chosen)
				&& !Chosen.IsEmpty())
			{
				Editor->SetModelPackagePath(Chosen);
			}
		}
	}
	return FReply::Handled();
}

ECheckBoxState SPoseKeyTimeline::GetUseGroupFrameRateCheckState() const
{
	const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin();
	return Editor.IsValid() && Editor->GetUseGroupFrameRateForPlayback()
		? ECheckBoxState::Checked
		: ECheckBoxState::Unchecked;
}

void SPoseKeyTimeline::OnUseGroupFrameRateChanged(ECheckBoxState NewState)
{
	if (const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin())
	{
		Editor->SetUseGroupFrameRateForPlayback(NewState == ECheckBoxState::Checked);
	}
}

ECheckBoxState SPoseKeyTimeline::GetTransferAuthoredTwistCheckState() const
{
	const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin();
	return Editor.IsValid() && Editor->GetTransferAuthoredTwist()
		? ECheckBoxState::Checked
		: ECheckBoxState::Unchecked;
}

void SPoseKeyTimeline::OnTransferAuthoredTwistChanged(ECheckBoxState NewState)
{
	if (const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin())
	{
		Editor->SetTransferAuthoredTwist(NewState == ECheckBoxState::Checked);
	}
}

FReply SPoseKeyTimeline::OnExportDiagnosticsClicked()
{
	if (const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin())
	{
		FString Path;
		FString Error;
		if (Editor->ExportGenerationDiagnostics(Path, Error))
		{
			DiagnosticsExportStatus = FString::Printf(TEXT("已导出：%s"), *Path);
		}
		else
		{
			DiagnosticsExportStatus = FString::Printf(TEXT("导出失败：%s"), *Error);
		}
	}
	return FReply::Handled();
}

FText SPoseKeyTimeline::GetDiagnosticsText() const
{
	const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin();
	return Editor.IsValid()
		? FText::FromString(Editor->GetGenerationDiagnosticsText())
		: FText::GetEmpty();
}

FText SPoseKeyTimeline::GetDiagnosticsExportStatusText() const
{
	return FText::FromString(DiagnosticsExportStatus);
}

FReply SPoseKeyTimeline::OnPrevFrameClicked()
{
	if (const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin())
	{
		Editor->StepPlayback(-1);
	}
	return FReply::Handled();
}

FReply SPoseKeyTimeline::OnNextFrameClicked()
{
	if (const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin())
	{
		Editor->StepPlayback(1);
	}
	return FReply::Handled();
}

FReply SPoseKeyTimeline::OnPrevKeyClicked()
{
	if (const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin())
	{
		Editor->JumpPlaybackToAdjacentKey(false);
	}
	return FReply::Handled();
}

FReply SPoseKeyTimeline::OnNextKeyClicked()
{
	if (const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin())
	{
		Editor->JumpPlaybackToAdjacentKey(true);
	}
	return FReply::Handled();
}

FReply SPoseKeyTimeline::OnPrevAnomalyClicked()
{
	const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin();
	if (Editor.IsValid() && !Editor->StepPlaybackToAdjacentAnomaly(-1))
	{
		// 静默不动最让人困惑：明确说「这边没有」，而不是假装按钮坏了。
		DiagnosticsExportStatus = TEXT("前面没有异常帧。");
	}
	return FReply::Handled();
}

FReply SPoseKeyTimeline::OnNextAnomalyClicked()
{
	const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin();
	if (Editor.IsValid() && !Editor->StepPlaybackToAdjacentAnomaly(1))
	{
		DiagnosticsExportStatus = TEXT("后面没有异常帧。");
	}
	return FReply::Handled();
}

FReply SPoseKeyTimeline::OnJumpToWorstFrameClicked()
{
	const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin();
	if (Editor.IsValid() && !Editor->JumpPlaybackToWorstFrame())
	{
		DiagnosticsExportStatus = TEXT("没有超过阈值的异常帧（位置 >5cm 或旋转 >45°）。");
	}
	return FReply::Handled();
}

float SPoseKeyTimeline::GetPlaybackSliderValue() const
{
	const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin();
	if (!Editor.IsValid())
	{
		return 0.0f;
	}
	const int32 FrameCount = Editor->GetGeneratedFrameCount();
	if (FrameCount <= 1)
	{
		return 0.0f;
	}
	const int32 Current = FMath::Max(0, Editor->GetCurrentPlaybackFrame());
	return FMath::Clamp(
		static_cast<float>(Current) / static_cast<float>(FrameCount - 1),
		0.0f, 1.0f);
}

void SPoseKeyTimeline::OnPlaybackSliderChanged(float Value)
{
	if (const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin())
	{
		const int32 FrameCount = Editor->GetGeneratedFrameCount();
		if (FrameCount > 0)
		{
			Editor->ScrubPlayback(FMath::RoundToInt(
				FMath::Clamp(Value, 0.0f, 1.0f)
				* static_cast<float>(FrameCount - 1)));
		}
	}
}

bool SPoseKeyTimeline::HasPlaybackFrames() const
{
	const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin();
	return Editor.IsValid() && Editor->GetGeneratedFrameCount() > 0;
}

FText SPoseKeyTimeline::GetPlaybackFrameText() const
{
	const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin();
	if (!Editor.IsValid() || Editor->GetGeneratedFrameCount() == 0)
	{
		return LOCTEXT("NoPlaybackFrames", "—");
	}
	const int32 Current = Editor->GetCurrentPlaybackFrame();
	const double FramesPerSecond = FMath::Max(
		1.0, static_cast<double>(Editor->GetPlaybackFramesPerSecond()));
	return FText::FromString(FString::Printf(
		TEXT("%d / %d 帧  %.2fs"),
		Current >= 0 ? Current + 1 : 0,
		Editor->GetGeneratedFrameCount(),
		(Current >= 0 ? Current : 0) / FramesPerSecond));
}

FText SPoseKeyTimeline::GetAnomalySummaryText() const
{
	const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin();
	if (!Editor.IsValid() || Editor->GetGeneratedFrameCount() == 0)
	{
		return LOCTEXT("AnomalyNoneYet", "尚未生成");
	}
	if (Editor->IsGenerationStale())
	{
		return LOCTEXT("AnomalyStale", "结果已过期");
	}
	const int32 Count = Editor->GetAnomalyFrameCount();
	if (Count == 0)
	{
		return LOCTEXT("AnomalyClean", "无异常帧");
	}
	return FText::FromString(FString::Printf(
		TEXT("异常帧 %d 个%s"),
		Count,
		Editor->IsCurrentPlaybackFrameAnomaly() ? TEXT(" · 当前帧异常") : TEXT("")));
}

FSlateColor SPoseKeyTimeline::GetAnomalySummaryColor() const
{
	const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin();
	if (!Editor.IsValid() || Editor->GetGeneratedFrameCount() == 0)
	{
		return FSlateColor(FLinearColor(0.55f, 0.55f, 0.55f));
	}
	if (Editor->GetAnomalyFrameCount() > 0)
	{
		return FSlateColor(FLinearColor(1.0f, 0.65f, 0.25f));
	}
	return FSlateColor(FLinearColor(0.45f, 0.85f, 0.45f));
}

FText SPoseKeyTimeline::GetKeyframeStripText() const
{
	const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin();
	if (!Editor.IsValid())
	{
		return FText::GetEmpty();
	}
	const UPoseGroupProxyAsset* Asset = Editor->GetAsset();
	if (Asset == nullptr || Asset->Keys.Num() == 0)
	{
		return LOCTEXT("NoKeys", "关键帧：无");
	}
	const double FrameRate = Asset->Settings.FrameRate;
	FString Text = TEXT("关键帧：");
	for (int32 Index = 0; Index < Asset->Keys.Num(); ++Index)
	{
		const double Seconds = FrameRate > 0.0
			? Asset->Keys[Index].Frame / FrameRate
			: 0.0;
		Text += FString::Printf(
			TEXT("%s%d=%.3fs"), Index > 0 ? TEXT("  ·  ") : TEXT(""),
			Index, Seconds);
	}
	switch (Asset->RefPoseGeneration.ReferencePattern)
	{
	case ERefPoseReferencePattern::Middle:
		Text += TEXT("    ｜ 硬参考：每窗口 offset 12（中间帧）");
		break;
	case ERefPoseReferencePattern::End:
		Text += TEXT("    ｜ 硬参考：每窗口 offset 23（末端帧）");
		break;
	case ERefPoseReferencePattern::MiddleAndEnd:
		Text += TEXT("    ｜ 硬参考：每窗口 offset 12 / 23（每 24 帧一次）");
		break;
	case ERefPoseReferencePattern::None:
	default:
		break;
	}
	return FText::FromString(Text);
}

FText SPoseKeyTimeline::GetPlaybackDebugText() const
{
	const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin();
	return Editor.IsValid()
		? FText::FromString(Editor->GetPlaybackDebugText())
		: FText::GetEmpty();
}

ECheckBoxState SPoseKeyTimeline::GetKeepKeyframesCheckState() const
{
	const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin();
	return Editor.IsValid() && Editor->GetShowKeyframesDuringPlayback()
		? ECheckBoxState::Checked
		: ECheckBoxState::Unchecked;
}

void SPoseKeyTimeline::OnKeepKeyframesChanged(ECheckBoxState NewState)
{
	if (const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin())
	{
		Editor->SetShowKeyframesDuringPlayback(
			NewState == ECheckBoxState::Checked);
	}
}

ECheckBoxState SPoseKeyTimeline::GetAuthorGhostCheckState() const
{
	const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin();
	return Editor.IsValid() && Editor->GetShowAuthorGhost()
		? ECheckBoxState::Checked
		: ECheckBoxState::Unchecked;
}

void SPoseKeyTimeline::OnAuthorGhostChanged(ECheckBoxState NewState)
{
	if (const TSharedPtr<FPoseGroupProxyAssetEditor> Editor = EditorPtr.Pin())
	{
		Editor->SetShowAuthorGhost(NewState == ECheckBoxState::Checked);
	}
}

#undef LOCTEXT_NAMESPACE
