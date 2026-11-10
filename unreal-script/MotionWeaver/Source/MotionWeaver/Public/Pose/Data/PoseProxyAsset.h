// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "Engine/DataAsset.h"
#include "Pose/Data/PoseTypes.h"
#include "PoseProxyAsset.generated.h"

class USkeletalMesh;
class USkeleton;
class UMaterialInterface;

/**
 * 姿态代理资产：一段动画在某一暂停帧上的全身姿态，独立存成一个 .uasset。
 *
 * 这份资产是给模型读取的输入原料，不参与任何动画图求值。
 * 拖进关卡时会生成 APoseActor，用这里记录的预览网格与材质把姿态显示出来。
 */
UCLASS(BlueprintType)
class MOTIONWEAVER_API UPoseProxyAsset final : public UDataAsset
{
	GENERATED_BODY()

public:
	/** 姿态本体。 */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Pose")
	FPoseFrameSnapshot Snapshot;

	/** 出处骨架；显示与校验都以它为准。 */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Pose|Source")
	TSoftObjectPtr<USkeleton> Skeleton;

	/** 抽取时预览用的网格，拖入关卡时自动沿用。 */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Pose|Display")
	TSoftObjectPtr<USkeletalMesh> PreviewMesh;

	/** 抽取时预览网格上的材质覆盖，拖入关卡时按同一顺序还原。 */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Pose|Display")
	TArray<TSoftObjectPtr<UMaterialInterface>> PreviewMaterials;

	/** 骨骼总数，便于读取端快速核对。 */
	UFUNCTION(BlueprintPure, Category = "Pose")
	int32 GetBoneCount() const { return Snapshot.BoneNames.Num(); }

	/** 姿态数据是否可用：版本受支持、骨名与变换数量一致且非空。 */
	UFUNCTION(BlueprintPure, Category = "Pose")
	bool IsValidPose() const
	{
		return Snapshot.IsSupportedVersion() && Snapshot.IsConsistent() && Snapshot.BoneNames.Num() > 0;
	}

#if WITH_EDITOR
	/** 编辑器显示用的资产描述，出现在内容浏览器与详情面板标题。 */
	virtual FString GetDesc() override;
#endif
};
