// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "Engine/DataAsset.h"
#include "Pose/Proxy/MotionWeaverPoseChainTypes.h"
#include "Misc/DataValidation.h"
#include "MotionWeaverPoseGroupAsset.generated.h"

class UMotionWeaverPoseAsset;

/**
 * 姿势代理组：把一串有序的姿势代理打成一个可复用的运动单元。
 *
 * 为什么需要它，而不是直接用关卡里的链 Actor：
 *  - 链 Actor 活在一个关卡里，换个关卡就得重摆一遍；跑酷这种动作是要复用的；
 *  - 关键帧数据（本地位置、朝向、帧号、停住标记）本身与关卡无关，
 *    它描述的是「相对起点的运动」，天然属于资产而不是关卡实例；
 *  - 资产可以被引用、被版本管理、被下游导出流程直接读到。
 *
 * 与链 Actor 的关系：Actor 是这份数据的**一个摆放实例**。
 * 资产是唯一真值，Actor 负责求解与显示。两者的字段刻意保持一致，
 * 但不要互相复制——需要改数据时改资产，再让 Actor 重新加载。
 *
 * 与单帧姿势代理的区别：UMotionWeaverPoseAsset 是「某一帧长什么样」，
 * 这里描述的是「这些帧按什么时间与位移串起来」。跑酷、翻越这类复合运动
 * 靠的是后者，单帧资产单独拿出来只能当静态摆件。
 */
UCLASS(BlueprintType, meta = (DisplayName = "MotionWeaver Pose Group"))
class MOTIONWEAVER_API UMotionWeaverPoseGroupAsset final : public UDataAsset
{
	GENERATED_BODY()

public:
	/** 组名，给人看的。留空时用资产名。 */
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "Group")
	FString DisplayName;

	/** 用途备注：跑酷 / 翻越 / 起身，便于在内容浏览器里筛。 */
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "Group")
	FString Usage;

	/** 有序关键帧。帧号必须严格递增，语义与链 Actor 上的 Keys 完全一致。 */
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "Group|Keys")
	TArray<FMotionWeaverPoseKey> Keys;

	/** 帧率与速度上限等求解参数。 */
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "Group|Settings")
	FMotionWeaverChainSettings Settings;

	/**
	 * 预备姿态：进入这段运动之前应该处于的状态。
	 * 为空表示不限制，衔接由调用方决定。
	 */
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "Group|Transition")
	TObjectPtr<UMotionWeaverPoseAsset> EntryPose = nullptr;

	/** 结束姿态：这段运动结束时应当抵达的状态。 */
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "Group|Transition")
	TObjectPtr<UMotionWeaverPoseAsset> ExitPose = nullptr;

	/** 关键帧数量。 */
	UFUNCTION(BlueprintPure, Category = "Group")
	int32 GetKeyCount() const { return Keys.Num(); }

	/**
	 * 结构是否可用于求解。
	 * 只检查数据自洽，不跑求解器——判据与求解器报错保持一致，
	 * 免得出现「资产说能用、一跑就报错」。
	 */
	UFUNCTION(BlueprintPure, Category = "Group")
	bool IsWellFormed() const;

	/** 总时长（秒）。按 Settings.FrameRate 与首尾帧号换算。 */
	UFUNCTION(BlueprintPure, Category = "Group")
	double GetDurationSeconds() const;

#if WITH_EDITOR
	virtual EDataValidationResult IsDataValid(FDataValidationContext& Context) const override;
#endif
};
