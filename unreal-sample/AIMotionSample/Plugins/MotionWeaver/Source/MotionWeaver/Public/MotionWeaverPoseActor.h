// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "MotionWeaverPoseActor.generated.h"

class UMotionWeaverPoseAsset;
class UMotionWeaverPoseComponent;

/**
 * 姿势代理：关卡里可摆放的一帧姿态。
 *
 * 行为上和静态网格体 Actor 一致——能被拖进场景、能摆变换、能被序列化进关卡；
 * 区别是它显示的不是网格资产而是某段动画某一帧的骨骼姿态。
 * 不参与 Tick，也不参与任何动画图求值。
 *
 * 姿态数据只存在组件上，Actor 侧是转发属性，避免两份真值互相失配。
 */
UCLASS(BlueprintType, meta = (DisplayName = "MotionWeaver Pose Proxy"))
class MOTIONWEAVER_API AMotionWeaverPoseActor final : public AActor
{
	GENERATED_BODY()

public:
	AMotionWeaverPoseActor();

	/** 姿态数据源；实际存放在 PoseComponent 上，这里转发读写。 */
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "Pose", meta = (DisplayName = "Pose Asset"))
	TObjectPtr<UMotionWeaverPoseAsset> PoseAsset;

	/** 实际承载并显示姿态的组件。 */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Pose")
	TObjectPtr<UMotionWeaverPoseComponent> PoseComponent;

	/** 换一份姿态数据并刷新显示。 */
	UFUNCTION(BlueprintCallable, Category = "Pose")
	void SetPoseAsset(UMotionWeaverPoseAsset* InPoseAsset);

protected:
	//~ Begin AActor Interface
	virtual void OnConstruction(const FTransform& Transform) override;
	virtual void PostLoad() override;
#if WITH_EDITOR
	virtual void PostEditChangeProperty(FPropertyChangedEvent& PropertyChangedEvent) override;
#endif
	//~ End AActor Interface
};
