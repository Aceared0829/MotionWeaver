// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "RefPoseModel.generated.h"

class UNNEModelData;
class USkeleton;

/** 骨架契约中的一节：骨名与父索引。 */
USTRUCT()
struct MOTIONWEAVER_API FRefPoseBoneContract
{
	GENERATED_BODY()

	UPROPERTY(VisibleAnywhere, Category = "RefPose")
	FName Name;

	UPROPERTY(VisibleAnywhere, Category = "RefPose")
	int32 Parent = INDEX_NONE;

	/** 参考姿态的父级相对平移（米，Y-up）。 */
	UPROPERTY(VisibleAnywhere, Category = "RefPose")
	FVector3d LocalPositionMeters = FVector3d::ZeroVector;

	/**
	 * 参考姿态的父级相对旋转（Y-up）。
	 *
	 * 只用于给「模型有、UE 没有」的骨（面部、末端）补一个与父骨连贯的姿态，
	 * 不参与位置解码，也不用来做 FK 重建模型输出。
	 */
	UPROPERTY(VisibleAnywhere, Category = "RefPose")
	FQuat4d LocalRotation = FQuat4d::Identity;
};

/**
 * RefPose 的 NNE 模型资产。
 *
 * 除了 ONNX 本身，还必须带上「模型到底吃什么」的契约：骨名顺序、父索引、
 * 参考姿态、位置归一化统计量、窗口长度。少任何一项，UE 侧都无法把输出
 * 还原成可蒙皮的姿态；而这些量都必须与权重来自同一次导出。
 */
UCLASS(BlueprintType)
class MOTIONWEAVER_API URefPoseModelAsset : public UObject
{
	GENERATED_BODY()

public:
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "RefPose")
	TObjectPtr<UNNEModelData> ModelData = nullptr;

	/** 该模型姿态对应的 UE 骨架；蒙皮前用它核对骨名。可为空（纯数值测试）。 */
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "RefPose")
	TObjectPtr<USkeleton> TargetSkeleton = nullptr;

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

	/** 模型契约里的帧率；不是整数 120，训练数据是 120.0048…。 */
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category = "RefPose")
	double FrameRate = 120.0;

	/** 骨名顺序与父索引，顺序即模型输出的骨骼槽位。 */
	UPROPERTY(VisibleAnywhere, Category = "RefPose|Contract")
	TArray<FRefPoseBoneContract> Bones;

	/** 位置归一化均值，长度 = BoneCount * 3。 */
	UPROPERTY(VisibleAnywhere, Category = "RefPose|Contract")
	TArray<float> PoseMean;

	/** 位置归一化标准差，长度 = BoneCount * 3。 */
	UPROPERTY(VisibleAnywhere, Category = "RefPose|Contract")
	TArray<float> PoseStd;

	/** 从磁盘创建未 Cook 的模型；只装 ONNX 与哈希。 */
	static URefPoseModelAsset* CreateFromOnnxFile(const FString& OnnxPath, FString& OutError);

	/**
	 * 从导出包目录补齐骨架契约与统计量。
	 *
	 * 读 skeleton.json、stats.bin 与 manifest.json 的 timing。
	 * 任一文件缺失或长度不符就返回 false，不做「猜一个默认值」——
	 * 猜错的统计量会让姿态整体偏移，猜错的帧率会让播放速度不对。
	 */
	bool LoadContractFromPackage(const FString& PackageDir, FString& OutError);

	/** 契约是否齐备到可以装配输入并解码输出。 */
	bool HasUsableContract() const;
};
