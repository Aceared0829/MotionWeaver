// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "Components/PoseableMeshComponent.h"
#include "MotionWeaverPoseComponent.generated.h"

class UMotionWeaverPoseAsset;

/**
 * 姿势代理的显示组件。
 *
 * 用 PoseableMeshComponent 而不是 SkeletalMeshComponent：我们不做动画求值，
 * 只把一份静态姿态写进骨骼，跳过整条动画图与动画实例的求值流程。
 * 组件自身不 Tick，姿态只在数据变化时刷新一次。
 */
UCLASS(ClassGroup = (MotionWeaver), meta = (BlueprintSpawnableComponent))
class MOTIONWEAVER_API UMotionWeaverPoseComponent final : public UPoseableMeshComponent
{
	GENERATED_BODY()

public:
	UMotionWeaverPoseComponent(const FObjectInitializer& ObjectInitializer);

	/** 使用的姿势资产；换资产会立即重建姿态。 */
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "Pose")
	TObjectPtr<UMotionWeaverPoseAsset> PoseAsset;

	/** 把资产的姿态写进骨骼。资产为空或数据不自洽时回退到参考姿态。 */
	UFUNCTION(BlueprintCallable, Category = "Pose")
	void ApplyPoseAsset(UMotionWeaverPoseAsset* InPoseAsset);

	/** 重新应用当前资产，例如资产在编辑器里被改动之后。 */
	UFUNCTION(BlueprintCallable, Category = "Pose")
	void RefreshFromAsset();

protected:
	//~ Begin UActorComponent Interface
	virtual void OnRegister() override;
	virtual void PostLoad() override;
#if WITH_EDITOR
	virtual void PostEditChangeProperty(FPropertyChangedEvent& PropertyChangedEvent) override;
#endif
	//~ End UActorComponent Interface

private:
	/** 按资产的骨骼名称逐骨写入局部变换；找不到的骨骼跳过。 */
	void ApplySnapshotToBones();
};
