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

	/**
	 * 模型旋转通道的稳定先验，长度 = BoneCount * 6（逐骨 6D）。
	 *
	 * 为什么需要它：`r.npy` 是 SOMA 骨架自己的局部旋转（骨轴沿该骨局部 +X），
	 * 与 UE 骨骼的局部旋转**不同源**（U1 未闭合）。把 UE 旋转直接写进这个
	 * 通道，会让模型看到位置与旋转互相矛盾的姿态：实测同一窗口位姿误差
	 * 0.95 m、重建骨长最大偏差 0.51 m（模型输出直接塌成一团）。
	 *
	 * 这里取模型包 fixtures 里真实训练样本的平均旋转作为先验：它保证旋转
	 * 通道始终落在训练分布内，身体姿态由位置通道表达。同一窗口实测位姿误差
	 * 降到 0.09 m、骨长最大偏差 0.12 m。
	 *
	 * 先验只用于**历史帧**。参考帧（mask 命中、会被硬覆盖进输出）仍用
	 * `EncodeRotationsFromPositions` 的方向编码，否则参考帧只剩位置、没有朝向。
	 * 等 U1 闭合、拿到逐骨旋转基变换后，两者都应改为真正的 UE→模型转换。
	 */
	UPROPERTY(VisibleAnywhere, Category = "RefPose|Contract")
	TArray<float> RotationPrior6D;

	/**
	 * 每根骨的「方向编码骨轴」：主子骨方向在该骨旋转坐标系里的平均表达。
	 *
	 * 由 fixtures 逐帧统计 `R^T · normalize(child - bone)` 的均值得到。
	 * 注意它**不是**「真实骨轴」：同一根骨在不同片段之间可以差 90–180°，
	 * 同一片段内也会随局部旋转变化（实测最大偏差 119°）。用它只能把
	 * 「骨朝哪个方向」单调地编码进 6D，不能还原训练标签本身的旋转。
	 *
	 * 因此它只用于**参考帧**的方向编码：`R = Swing(Axis, currentDirection)`。
	 * 历史帧改用 `RotationPrior6D`，不要在历史里用它——会把分布外数值
	 * 喂回模型，实测整段姿态塌陷。
	 */
	UPROPERTY(VisibleAnywhere, Category = "RefPose|Contract")
	TArray<FVector3d> RotationAxis;

	/** RotationAxis 对应的子骨槽位；叶子骨为 INDEX_NONE。 */
	UPROPERTY(VisibleAnywhere, Category = "RefPose|Contract")
	TArray<int32> RotationChild;

	/** 旋转通道标定是否可用（三个数组长度一致）。 */
	bool HasRotationCalibration() const;

	/**
	 * 由逐骨位置编码旋转通道。
	 *
	 * 只接受与契约同骨的米制 Y-up 位置；叶子骨沿用父骨的旋转。
	 * 标定缺失时返回 false，调用方必须显式选择回退路径，不能静默写零。
	 */
	bool EncodeRotationsFromPositions(
		TConstArrayView<FVector3d> PositionsMeters,
		TArray<float>& OutRotations6D) const;

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
