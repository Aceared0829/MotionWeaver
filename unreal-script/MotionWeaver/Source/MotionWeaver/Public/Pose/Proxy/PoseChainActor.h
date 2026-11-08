// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "Pose/Proxy/PoseChainTypes.h"
#include "PoseChainActor.generated.h"

class UPoseChainDebugComponent;
class UPoseComponent;
class UPoseProxyAsset;

/**
 * 姿态代理：关卡里可摆放的一条有序姿态链。
 *
 * 这是「代理模式」的编辑载体。它做四件事：
 *  1. 存一串关键帧（姿势资产 + 局部位置 + 偏航 + 帧号 + 是否停住）；
 *  2. 调求解器把稀疏关键帧算成稠密地面轨迹；
 *  3. 每个关键帧摆一个真蒙皮姿势（资产没预览网格时退回骨架线）；
 *  4. 把轨迹交给调试组件画出来（轨迹线 + 方向箭头）。
 *
 * 刻意**不**继承 APoseActor：那个 Actor 的语义是「一个 Actor = 一个姿势」，
 * 全部价值在单帧显示，链 Actor 要的是「一个 Actor = 多个姿势」，
 * 两者的组件假设不兼容。它们只共用 UPoseProxyAsset 这个资产契约。
 *
 * 显示分两层：
 *  - 轨迹层（UPoseChainDebugComponent）永远存在，画曲线与火柴人；
 *  - 姿势层在编辑器里由 APoseChainKeyProxy 承担——每个 key 一个代理
 *    Actor，这样「点中角色拖 gizmo」走的是引擎现成的 AActor 路径。
 *    没有代理时（打包构建）退回链自己持有的 UPoseComponent。
 * 两者都不 Tick、不求值动画图——姿态是直接写进骨骼的静态数据。
 */
UCLASS(BlueprintType, meta = (DisplayName = "MotionWeaver Pose Chain"))
class MOTIONWEAVER_API APoseChainActor final : public AActor
{
	GENERATED_BODY()

public:
	APoseChainActor();

	/** 有序关键帧。帧号必须严格递增；求解时按帧号排序，顺序即时间。 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Chain")
	TArray<FPoseKey> Keys;

	/** 帧率与各项上限。 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Chain")
	FChainSettings Settings;

	/** 改动任何 key 或参数后自动重算。关掉可以手动点 Solve。 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Chain")
	bool bAutoSolve = true;

	/** 是否显示轨迹、箭头与姿势。 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Display")
	bool bShowChain = true;

	/** 最近一次求解的结果。只读，供查看与下游导出。 */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Chain|Result")
	FChainResult Result;

	/** 重算并刷新显示。bAutoSolve 打开时会在改动后自动调用。 */
	UFUNCTION(BlueprintCallable, Category = "Chain")
	void SolveChain();

	/**
	 * 追加一条关键帧并立即刷新。
	 * 帧号取「最后一个 key 的帧号 + Settings.NewKeyFrameStride」，
	 * 因此从视口连续拖入多个姿势不会撞到重复帧号。
	 * @return 新条目的下标。
	 */
	UFUNCTION(BlueprintCallable, Category = "Chain")
	int32 AppendKey(UPoseProxyAsset* PoseAsset, const FVector& LocalLocation, double HeadingDegrees);

	/** 稠密轨迹，相对本 Actor。 */
	UFUNCTION(BlueprintPure, Category = "Chain")
	const TArray<FChainTrajectorySample>& GetSamples() const { return Result.Samples; }

	/**
	 * 编辑器代理 Actor 拖完之后的唯一写回点。
	 *
	 * 位置和朝向在同一次写入里落地，只触发一次 Rebuild：
	 * 分两次写会出现「位置已更新、朝向还是旧的」的中间态被画出来。
	 * @param SortedIndex 排序后的 key 下标（与代理的 SortedIndex 同一套）
	 * @param NewBodyRotation 完整朝向；Yaw 进求解器，Pitch/Roll 只影响显示
	 */
	void ApplyProxyTransform(int32 SortedIndex, const FVector& NewLocalLocation, const FRotator& NewBodyRotation);

	/** 最近一次的体检报告。 */
	UFUNCTION(BlueprintPure, Category = "Chain")
	const FChainDiagnostics& GetDiagnostics() const { return Result.Diagnostics; }

protected:
	//~ Begin AActor Interface
	virtual void OnConstruction(const FTransform& Transform) override;
	virtual void PostLoad() override;
#if WITH_EDITOR
	virtual void PostEditChangeProperty(FPropertyChangedEvent& PropertyChangedEvent) override;
#endif
	//~ End AActor Interface

private:
	/** 按当前 Keys 与 Settings 重算，并把结果推给调试组件与姿势组件。 */
	void Rebuild();

	/** 让姿势组件数量与 Keys 对齐，并逐个应用姿态。 */
	void SyncPoseComponents(const TArray<FPoseKey>& SortedKeys);

	/**
	 * 只刷新显示层的变换，不重解轨迹。
	 * 用于 Pitch/Roll 这类「不影响 Root 路径、但网格要转」的改动。
	 */
	void SyncDisplayOnly();

	/** 实际承担轨迹绘制的组件。 */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Display", meta = (AllowPrivateAccess = "true"))
	TObjectPtr<UPoseChainDebugComponent> DebugComponent;

	/**
	 * 每个关键帧一个蒙皮组件，运行时按需增删。
	 * 头文件里声明成 transient：它是显示产物，不该被序列化进关卡，
	 * 重建时按 Keys 重新生成，避免存下与 Keys 不一致的陈旧副本。
	 */
	UPROPERTY(Transient)
	TArray<TObjectPtr<UPoseComponent>> PoseComponents;

	/**
	 * 按帧号排好序的键，与 PoseComponents 同下标。
	 * 编辑器手柄用它把「第几条」映射回 Keys 里的下标，避免每次都重排。
	 */
	TArray<FPoseKey> SortedKeysCache;

	/** 排序后的下标 -> Keys 里的原始下标。 */
	TArray<int32> SortedToOriginal;

#if WITH_EDITORONLY_DATA
	/**
	 * 每个 key 在编辑器里对应一个代理 Actor，让「点中角色、拖 gizmo」走引擎现成的
	 * AActor 那条路。仅编辑器存在，不参与序列化，重建时按 Keys 重新生成。
	 */
	UPROPERTY(Transient)
	TArray<TObjectPtr<class APoseChainKeyProxy>> ProxyActors;

	/** 生成 / 复用 / 销毁代理 Actor，使数量与 SortedKeysCache 对齐。 */
	void RebuildProxyActors();

	/** 只把变换推给已有代理（拖动时不重建、不重灌蒙皮）。 */
	void SyncProxyTransforms();

	/** bShowProxyDebug 打开时，把代理状态画到视口上。 */
	void DrawProxyDebug() const;

	/** 编辑器里代理是否显示。关掉可以退回只看轨迹线与火柴人。 */
	UPROPERTY(EditAnywhere, Category = "Display")
	bool bShowEditableProxies = true;

	/** 在视口左上角与每个代理头顶打出诊断文本，用于排查「点不中」。 */
	UPROPERTY(EditAnywhere, Category = "Display|Debug")
	bool bShowProxyDebug = false;
#endif
};
