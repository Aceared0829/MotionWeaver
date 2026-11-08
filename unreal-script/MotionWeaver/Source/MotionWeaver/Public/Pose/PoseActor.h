// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "PoseActor.generated.h"

class UPoseProxyAsset;
class UPoseComponent;

/**
 * 姿态代理：关卡里可摆放的一帧姿态。
 *
 * 行为上和静态网格体 Actor 一致——能被拖进场景、能摆变换、能被序列化进关卡；
 * 区别是它显示的不是网格资产而是某段动画某一帧的骨骼姿态。
 * 不参与 Tick，也不参与任何动画图求值。
 *
 * 姿态数据由组件持有。这里的 PoseAsset 属性带 Getter/Setter：
 * 读取一律取组件那份，写入会同步灌进组件，因此不存在两处真值互相失配。
 * 字段本身仍作为序列化镜像保存（组件尚未创建时 Getter 需要它）。
 */
UCLASS(BlueprintType, meta = (DisplayName = "Pose Proxy"))
class MOTIONWEAVER_API APoseActor final : public AActor
{
	GENERATED_BODY()

public:
	APoseActor();

	/** 姿态数据源。实际存放在 PoseComponent 上，这里转发读写。 */
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "Pose", Getter, Setter)
	TObjectPtr<UPoseProxyAsset> PoseAsset;

	/** 实际承载并显示姿态的组件。 */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Pose")
	TObjectPtr<UPoseComponent> PoseComponent;

	/** PoseAsset 的读取：始终以组件为准，保证唯一真值。 */
	UFUNCTION(BlueprintGetter, Category = "Pose")
	UPoseProxyAsset* GetPoseAsset() const;

	/** PoseAsset 的写入：写进组件并立即刷新显示。 */
	UFUNCTION(BlueprintSetter, Category = "Pose")
	void SetPoseAsset(UPoseProxyAsset* InPoseAsset);

protected:
	//~ Begin AActor Interface
	virtual void OnConstruction(const FTransform& Transform) override;
	virtual void PostLoad() override;
	//~ End AActor Interface
};
