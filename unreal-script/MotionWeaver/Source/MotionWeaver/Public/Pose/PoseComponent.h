// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "Components/PoseableMeshComponent.h"
#include "PoseComponent.generated.h"

class UPoseProxyAsset;

/**
 * 姿势代理的显示组件。
 *
 * 用 PoseableMeshComponent 而不是 SkeletalMeshComponent：我们不做动画求值，
 * 只把一份静态姿态写进骨骼，跳过整条动画图与动画实例的求值流程。
 * 组件自身不 Tick，姿态只在数据变化时刷新一次。
 */
UCLASS(ClassGroup = (MotionWeaver), meta = (BlueprintSpawnableComponent))
class MOTIONWEAVER_API UPoseComponent final : public UPoseableMeshComponent
{
	GENERATED_BODY()

public:
	UPoseComponent(const FObjectInitializer& ObjectInitializer);

	/** 使用的姿势资产；换资产会立即重建姿态。 */
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "Pose")
	TObjectPtr<UPoseProxyAsset> PoseAsset;

	/** 把资产的姿态写进骨骼并还原材质。资产为空或数据不自洽时回退到参考姿态。 */
	UFUNCTION(BlueprintCallable, Category = "Pose")
	void ApplyPoseAsset(UPoseProxyAsset* InPoseAsset);

	/**
	 * 只登记资产，不做渲染侧工作。
	 * 用于属性 setter：细节面板提交后紧跟着会重跑构造脚本重注册组件，
	 * 在那里做实现会重复劳动，实际应用交给 OnConstruction / OnRegister。
	 */
	void SetPoseAssetDeferred(UPoseProxyAsset* InPoseAsset);

	/** 重新应用当前资产，例如资产在编辑器里被改动之后。 */
	UFUNCTION(BlueprintCallable, Category = "Pose")
	void RefreshFromAsset();

	/** 按资产记录的槽位顺序还原预览材质。关卡实例与编辑器视口共用这一份实现。 */
	UFUNCTION(BlueprintCallable, Category = "Pose")
	void ApplyPreviewMaterials();

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
