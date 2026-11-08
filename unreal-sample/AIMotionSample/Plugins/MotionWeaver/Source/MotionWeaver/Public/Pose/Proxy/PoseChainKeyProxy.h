// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "PoseChainKeyProxy.generated.h"

class APoseChainActor;
class UPoseProxyAsset;
class UPoseComponent;

/**
 * 一个关键帧的「可拖代理」。
 *
 * 为什么需要它：链的真值是一个 Keys 数组，蒙皮组件只是它的派生显示。
 * 因此在视口里点住角色、拖 gizmo 这件事在数据上没有落点——
 * 蒙皮组件不是组件可视化器的宿主，点它命中的是 HActor，
 * 而引擎把组件选中这件事挡在 CanCreateBlueprintOfClass 后面，
 * 链 Actor 是 final + BlueprintType，那道门永远打不开。
 *
 * 换个做法：每个 key 在这里投影成一个真正的 Actor。
 * 于是「点中、拖 gizmo、框选、撤销」全部走引擎现成的 AActor 那条路，
 * 不需要自己维护命中代理、坐标系统与选择状态。
 *
 * 写回只有一个入口：PostEditMove，把世界变换逆回链局部存进 Keys。
 * 代理本身不进大纲、不进游戏视图，由链 Actor 全权管理生命周期。
 * 「不序列化」是靠 SpawnParameters.ObjectFlags |= RF_Transient 实现的（实例级），
 * 不能写成 UCLASS(Transient)——那是类级标记，会让 UnrealEd 直接拒绝把该类放进关卡：
 * "Class ... is transient. You can't add actors of this class in UnrealEd."
 *
 * 放在 Runtime 模块而不是 Editor 模块，是因为链 Actor 在 Runtime 模块里
 * 要持有与生成它，而 Runtime 依赖 Editor 会形成反向依赖。
 * 真正只在编辑器里发生的部分（生成时机）用 WITH_EDITORONLY_DATA 隔开。
 */
UCLASS(NotBlueprintable, meta = (DisplayName = "Pose Chain Key Proxy"))
class MOTIONWEAVER_API APoseChainKeyProxy final : public AActor
{
	GENERATED_BODY()

public:
	APoseChainKeyProxy();

	/** 显示该帧姿态的组件。与链上的蒙皮共用同一份资产契约。 */
	UPROPERTY(Transient, VisibleAnywhere, Category = "Proxy")
	TObjectPtr<UPoseComponent> PoseDisplay;

	/** 所属的链。弱引用：代理比链先析构时不会悬挂。 */
	TWeakObjectPtr<APoseChainActor> OwningChain;

	/** 这条代理对应排序后的第几个 key（与链的 SortedToOriginal 同一套下标）。 */
	int32 SortedIndex = INDEX_NONE;

	/**
	 * 由链在重建时调用：摆好位置与完整朝向，按需要重灌姿态。
	 * bForcePoseRefresh 为真时重灌姿态（资产换了要重画），否则只摆变换。
	 */
	void SyncFromChain(
		const FVector& LocalLocation,
		const FRotator& BodyRotation,
		UPoseProxyAsset* PoseAsset,
		bool bVisible,
		bool bForcePoseRefresh);

protected:
	//~ Begin AActor Interface
	virtual void PostEditMove(bool bFinished) override;
	//~ End AActor Interface

private:
	/** 上一次从链同步过来的世界变换，用来挡住回写引起的自激。 */
	FVector LastSyncedWorldLocation = FVector(ForceInitToZero);
	FRotator LastSyncedWorldRotation = FRotator::ZeroRotator;

	/** 同步期间置位，PostEditMove 直接返回。 */
	bool bIsSyncingFromChain = false;
};
