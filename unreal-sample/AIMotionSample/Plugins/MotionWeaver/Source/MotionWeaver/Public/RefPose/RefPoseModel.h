// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "RefPoseModel.generated.h"

class UNNEModelData;

/** RefPose 的 NNE 模型资产；阶段二只承载 ONNX，不负责骨架或坐标转换。 */
UCLASS(BlueprintType)
class MOTIONWEAVER_API URefPoseModelAsset : public UObject
{
	GENERATED_BODY()

public:
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "RefPose")
	TObjectPtr<UNNEModelData> ModelData = nullptr;

	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "RefPose")
	FString CheckpointSha256;

	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "RefPose")
	FString OnnxSha256;

	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "RefPose")
	int32 BoneCount = 78;

	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "RefPose")
	int32 HistoryFrames = 24;

	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "RefPose")
	int32 FutureFrames = 24;

	/** 从磁盘创建未 Cook 的模型，供本地数值测试及后续编辑器导入使用。 */
	static URefPoseModelAsset* CreateFromOnnxFile(const FString& OnnxPath, FString& OutError);
};
