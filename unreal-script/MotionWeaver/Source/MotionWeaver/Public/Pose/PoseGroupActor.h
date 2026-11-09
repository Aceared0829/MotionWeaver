// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "PoseGroupActor.generated.h"

class UPoseChainDebugComponent;
class UPoseComponent;
class UPoseGroupProxyAsset;

/**
 * 姿态代理组在世界里的一个摆放实例。
 *
 * 这个 Actor 只做显示，不做编辑。
 *
 * 数据全部存在 UPoseGroupProxyAsset 里（Keys、Settings、求解结果）。
 * 编辑全部在资产编辑器里做（时间轴、gizmo、增删关键帧）。
 * 这里只负责把资产已经算好的东西摆出来：每个关键帧一个蒙皮，
 * 以及轨迹线与方向箭头。
 *
 * 为什么不在这里做编辑：这个 Actor 活在关卡里，要拿到 gizmo 就得跟
 * 引擎的 Actor 选择系统打交道——而组件选中被 CanCreateBlueprintOfClass
 * 挡住，per-key 代理又会导致一次拖动触发全链重算。那条路试过，
 * 结论是不要在别人的选择系统里造编辑器。资产编辑器里视口是自己的，
 * 命中测试与拖拽都由自己实现，可控得多。
 *
 * 不 Tick、不求值动画图：姿态是直接写进骨骼的静态数据。
 */
UCLASS(BlueprintType, meta = (DisplayName = "Pose Group Actor"))
class MOTIONWEAVER_API APoseGroupActor final : public AActor
{
	GENERATED_BODY()

public:
	APoseGroupActor();

	/** 数据源。整个 Actor 的显示内容都由它决定；为空时什么都不画。 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Pose")
	TObjectPtr<UPoseGroupProxyAsset> GroupAsset;

	/** 是否显示轨迹、箭头与姿势。 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Display")
	bool bShowGroup = true;

	/** 换资产或资产内容变化后刷新显示。 */
	UFUNCTION(BlueprintCallable, Category = "Pose")
	void RefreshFromAsset();

	/** 稠密轨迹，相对本 Actor。没有资产时为空。 */
	UFUNCTION(BlueprintPure, Category = "Pose")
	const TArray<FChainTrajectorySample>& GetSamples() const;

protected:
	//~ Begin AActor Interface
	virtual void OnConstruction(const FTransform& Transform) override;
	virtual void PostLoad() override;
#if WITH_EDITOR
	virtual void PostEditChangeProperty(FPropertyChangedEvent& PropertyChangedEvent) override;
#endif
	//~ End AActor Interface

private:
	/** 按资产的当前内容重建显示：蒙皮数量对齐、轨迹数据推给调试组件。 */
	void SyncDisplay();

	/** 删除所有蒙皮组件。换资产或资产被清空时用。 */
	void ClearPoseComponents();

	/** 实际承担轨迹绘制的组件。 */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Display", meta = (AllowPrivateAccess = "true"))
	TObjectPtr<UPoseChainDebugComponent> DebugComponent;

	/**
	 * 每个关键帧一个蒙皮组件，运行时按需增删。
	 * Transient：它是资产的派生显示，不该被序列化进关卡。
	 */
	UPROPERTY(Transient)
	TArray<TObjectPtr<UPoseComponent>> PoseComponents;
};
