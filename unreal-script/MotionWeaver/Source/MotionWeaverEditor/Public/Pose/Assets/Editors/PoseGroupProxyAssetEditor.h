// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "Pose/Data/PoseChainTypes.h"
#include "RefPose/RefPoseGenerator.h"
#include "RefPose/RefPoseModel.h"
#include "Toolkits/AssetEditorToolkit.h"
#include "UObject/StrongObjectPtr.h"

class UPoseGroupProxyAsset;
class UPoseKeyAssetEditorContext;
class SPoseGroupViewport;
class SPoseKeyTimeline;
class IDetailsView;
class SDockTab;
struct FPropertyChangedEvent;

/**
 * 姿态代理组的编辑器：左边视口看姿态与轨迹，中间时间轴排帧，右边细节面板。
 *
 * 这是 MotionWeaver 唯一的编辑入口。关卡里的 APoseGroupActor 只读不写。
 *
 * 为什么编辑放在这里而不是关卡：
 *  - 关卡里要拿 gizmo 就得跟引擎的 Actor 选择系统打交道，而组件选中
 *    被 CanCreateBlueprintOfClass 挡死（Actor 是 final + BlueprintType）；
 *  - 这里视口是自己实现的，命中测试、拖拽、选择全由自己掌控。
 */
class FPoseGroupProxyAssetEditor final : public FAssetEditorToolkit
{
public:
	/** 打开指定资产的编辑器。 */
	void InitPoseGroupProxyAssetEditor(
		const EToolkitMode::Type Mode,
		const TSharedPtr<IToolkitHost>& InitToolkitHost,
		UPoseGroupProxyAsset* InAsset);

	/** 当前选中的关键帧下标（时间轴与视口共用）。 */
	int32 GetSelectedKeyIndex() const { return SelectedKeyIndex; }

	/**
	 * 切换当前编辑的关键帧。
	 * 视口、时间轴、细节面板三方都跟着走，避免出现「视口显示第 2 帧
	 * 但时间轴高亮第 0 帧」这种各说各话的状态。
	 */
	void SetSelectedKeyIndex(int32 InKeyIndex);

	/** 资产内容变了（增删帧、改帧号）之后，让三方重新读一次。 */
	void RefreshFromAsset();

	/** 时间轴要读资产。 */
	UPoseGroupProxyAsset* GetAsset() const { return GroupAsset; }

	//~ Begin FAssetEditorToolkit Interface
	virtual void RegisterTabSpawners(const TSharedRef<FTabManager>& InTabManager) override;
	virtual void UnregisterTabSpawners(const TSharedRef<FTabManager>& InTabManager) override;
	virtual FName GetToolkitFName() const override;
	virtual FText GetBaseToolkitName() const override;
	virtual FText GetToolkitName() const override;
	virtual FString GetWorldCentricTabPrefix() const override;
	virtual FLinearColor GetWorldCentricTabColorScale() const override;
	virtual void OnClose() override;
	//~ End FAssetEditorToolkit Interface

	/** 往当前资产追加一个空关键帧，帧号接在最后一个之后。 */
	void AddKey();

	/** 删除选中的关键帧。 */
	void DeleteSelectedKey();

	/** 把选中帧的帧号改动写回资产并重解。 */
	void SetSelectedKeyFrame(int32 NewFrame);

	/** 把指定关键帧的帧号改动写回资产并重解。 */
	void SetKeyFrame(int32 KeyIndex, int32 NewFrame);

	/** 按秒改指定关键帧的时间；帧号 = round(秒 × 帧率)。 */
	void SetKeyTimeSeconds(int32 KeyIndex, double Seconds);

	/** 视口拖拽：把新值写回选中帧（拖动中不重解）。 */
	void ApplyDraggedKey(const FPoseKey& NewKeyValue);

	/** 松手：求解整条链并刷新时间轴与轨迹。 */
	void FinishDrag();

	/** 拖拽中：把资产里的新位置同步到预览组件（视图跟随）。 */
	void SyncPreviewFromAsset();

	/** 接上/断开元素层的拖动通知。 */
	void BindElementMovement();
	void UnbindElementMovement();

	/** 撤销/重做之后重建视图：资产的 Key 被回滚了，蒙皮要重新贴。 */
	void OnPostUndoRedo();

	/**
	 * 生成并播放：用姿态组求解出的 Root 轨迹与关键帧参考姿态驱动模型，
	 * 生成中间姿态并在视口播放。
	 *
	 * 首版是「离线生成整段再播放」，不是实时滚动推理。
	 */
	void GenerateAndPlay();

	/** 播放/暂停切换。 */
	void TogglePlayback();

	/** 停止并回到起点。 */
	void StopPlayback();

	/** 生成结果是否已过期（资产或模型改过）。 */
	bool IsGenerationStale() const { return bGenerationStale; }

	/** 最近一次生成是否失败。时间轴用它把状态文字标红。 */
	bool HasGenerationError() const { return bLastGenerationFailed; }

	/** 播放状态文字，供时间轴显示。 */
	FString GetPlaybackStatusText() const;

	/** 标记当前生成结果已过期，播放前必须重新生成。 */
	void MarkGenerationStale();

	/** Tick：推进播放游标。返回 true 表示本帧有新姿态。 */
	bool TickPlayback(float DeltaSeconds);

	float GetPlaybackFramesPerSecond() const;

private:
	TSharedRef<SDockTab> SpawnTab_Viewport(const FSpawnTabArgs& Args);
	TSharedRef<SDockTab> SpawnTab_Timeline(const FSpawnTabArgs& Args);
	TSharedRef<SDockTab> SpawnTab_Details(const FSpawnTabArgs& Args);

	/** 细节面板改完资产后，让幽灵组件与时间轴跟上。 */
	void OnAssetPropertyChanged(const FPropertyChangedEvent& PropertyChangedEvent);

	/** 把姿态组的关键帧与求解轨迹转成模型输入并生成。 */
	bool BuildAndGenerate(FString& OutError);

	/**
	 * 确保 RefPose 模型已加载。
	 *
	 * 当前阶段还没有模型导入资产，模型包由环境变量
	 * `MW_REFPOSE_UE_PACKAGE` 或 `Saved/MotionWeaver/RefPosePackage.txt` 指定。
	 * 同一路径只解析一次，由 TStrongObjectPtr 持有；路径变了会重新加载。
	 */
	bool EnsureModelAsset(FString& OutError);

	/** 播放时钟。 */
	double PlaybackTimeSeconds = 0.0;
	bool bIsPlaying = false;
	bool bGenerationStale = true;

	/** 生成诊断，显示在编辑器状态里。 */
	FString GenerationStatus;

	/** 最近一次生成的非阻断告警（轨迹超速、帧率不一致等）。 */
	TArray<FString> GenerationWarnings;

	/** 最近一次生成是否失败；失败时状态文字必须可见。 */
	bool bLastGenerationFailed = false;

	/** 当前 ModelAsset 对应的包目录；包目录变了要重新加载，而不是继续用旧会话。 */
	FString LoadedPackageDir;

	/** 编辑器 Tick 句柄；编辑器打开期间常驻，内部按 bIsPlaying 早退。 */
	FTSTicker::FDelegateHandle PlaybackTickerHandle;
	bool TickPlaybackTick(float DeltaSeconds);

	static const FName ViewportTabId;
	static const FName TimelineTabId;
	static const FName DetailsTabId;

	UPoseGroupProxyAsset* GroupAsset = nullptr;

	/**
	 * 把「当前选中帧」暴露成选择集，供编辑器变换手柄取用。
	 * 没有它手柄找不到任何操作对象。
	 */
	UPROPERTY(Transient)
	TObjectPtr<UPoseKeyAssetEditorContext> ElementContext;

	TSharedPtr<SPoseGroupViewport> ViewportWidget;
	TSharedPtr<SPoseKeyTimeline> TimelineWidget;
	TSharedPtr<IDetailsView> DetailsView;

	int32 SelectedKeyIndex = INDEX_NONE;

	/** 拖动通知的绑定句柄，关闭编辑器时要解绑。 */
	TArray<FDelegateHandle> MovementHandles;

	/** 撤销/重做事件的绑定句柄。 */
	FDelegateHandle UndoRedoHandle;

	/** 细节面板属性变更的绑定句柄。 */
	FDelegateHandle DetailsPropertyHandle;

	bool bMovementTransactionOpen = false;

	/**
	 * 当前使用的模型资产。
	 *
	 * 编辑器不负责导入模型：它只消费已经在资产里配好的 RefPose 模型。
	 * 没配模型时「生成并播放」应当明确报错，而不是静默生成静态姿态。
	 */
	/** 强引用：编辑器不是 UObject，普通 UPROPERTY 不会被 GC 扫描。 */
	TStrongObjectPtr<URefPoseModelAsset> ModelAsset;

	/** 生成结果（每帧每骨 UE 局部变换）。 */
	TArray<TArray<FTransform>> GeneratedFrames;

	/** 没有映射到 UE 骨架的模型骨数（手指、面部、末端骨）。 */
	int32 UnmappedBoneCount = 0;

	/** 与 GeneratedFrames 对齐的诊断。 */
	FRefPoseGenerationMetrics GenerationMetrics;
};
