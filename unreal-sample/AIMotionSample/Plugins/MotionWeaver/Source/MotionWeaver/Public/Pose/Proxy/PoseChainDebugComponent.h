// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "Debug/DebugDrawComponent.h"
#include "Pose/Proxy/PoseChainTypes.h"
#include "PoseChainDebugComponent.generated.h"

/**
 * 姿态链的显示组件。
 *
 * 用 UDebugDrawComponent 而不是网格：这一层要画的是「轨迹线 + 方向箭头 + 火柴人」，
 * 没有任何蒙皮需求。火柴人由姿势资产的局部变换就地做一次 FK 得到，
 * 因此全链 N 个姿势也只是几万条线段，不需要加载网格与材质。
 *
 * 数据在游戏线程算好（SetChainData），场景代理只负责把已经算好的线段搬出去。
 * 组件自己不 Tick。
 */
UCLASS(ClassGroup = (MotionWeaver), meta = (BlueprintSpawnableComponent))
class MOTIONWEAVER_API UPoseChainDebugComponent : public UDebugDrawComponent
{
	GENERATED_BODY()

public:
	UPoseChainDebugComponent(const FObjectInitializer& ObjectInitializer);

	/** 一帧火柴人的骨点：已经做过 FK，坐标相对该 key 的原点。 */
	struct FStickFigure
	{
		/** 关键帧在链局部空间里的位置（始终有值，与有没有骨点无关）。 */
		FVector Origin = FVector::ZeroVector;

		/** 骨点，已做过 FK。为空表示画不出骨架。 */
		TArray<FVector> BonePoints;

		/** 每根骨的父骨下标，与 BonePoints 一一对应。 */
		TArray<int32> ParentIndices;

		/**
		 * 这一条已经有真蒙皮在显示了。
		 * 有它就不再画骨架线，否则线会穿在角色身上，看着像故障。
		 */
		bool bHasSkinnedPose = false;
	};

	/**
	 * 交出显示所需的一切。组件只存副本，不引用关卡的 Actor。
	 * @param InSamples       稠密轨迹（相对 Actor 的局部坐标）
	 * @param InKeys          创作关键帧
	 * @param InDerivedKeys   自动插入的派生关键帧
	 * @param InStickFigures  每个 key 的火柴人，和 InKeys 一一对应；没有姿势的条目为空
	 * @param InSettings      只用到箭头间隔、箭头长度、是否画火柴人
	 */
	void SetChainData(
		const TArray<FChainTrajectorySample>& InSamples,
		const TArray<FPoseKey>& InKeys,
		const TArray<FDerivedKey>& InDerivedKeys,
		const TArray<FStickFigure>& InStickFigures,
		const FChainSettings& InSettings);

	/** 从姿势资产就地算出一帧火柴人。资产为空或骨架不可用时返回空骨点。 */
	static FStickFigure BuildStickFigure(const class UPoseProxyAsset* PoseAsset, const FVector& Origin);

protected:
	//~ Begin UPrimitiveComponent Interface
	virtual FDebugRenderSceneProxy* CreateDebugSceneProxy() override;
	virtual FBoxSphereBounds CalcBounds(const FTransform& LocalToWorld) const override;
	//~ End UPrimitiveComponent Interface

private:
	TArray<FChainTrajectorySample> Samples;
	TArray<FPoseKey> Keys;
	TArray<FDerivedKey> DerivedKeys;
	TArray<FStickFigure> StickFigures;
	FChainSettings Settings;

	/** 旧版数据里统计出来的包围盒，CalcBounds 用。 */
	FBox LocalBounds = FBox(ForceInit);
};
