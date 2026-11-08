// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"

#include "UObject/ObjectPtr.h"

#include "MotionWeaverPoseChainTypes.generated.h"

class UMotionWeaverPoseAsset;

/**
 * 姿态链上的一个关键条目。
 *
 * 朝向的真值是 BodyRotation（完整 FRotator），HeadingDegrees 是它的派生缓存。
 * 为什么这样分：
 *  - 授权来源只有一个：编辑器里按 E 转出来的是完整旋转，不该被两个字段瓜分；
 *  - 但 Root 轨迹按模型契约只吃偏航（contract 2.6：独立 Root 轨迹竖直分量恒 0，
 *    k[:,1] 恒 0，俯仰翻滚全部待在身体姿态里），所以 HeadingDegrees 仍然只承载 Yaw。
 * 求解器读 HeadingDegrees，显示层（代理 Actor / 蒙皮组件）读 BodyRotation。
 * 两者由 SetHeadingDegrees / SetBodyRotation 成对维护，不会各说各话。
 *
 * 位置是**相对本 Actor 的局部坐标**（cm），拖 Actor 时整条链跟着走。
 */
USTRUCT(BlueprintType)
struct MOTIONWEAVER_API FMotionWeaverPoseKey
{
	GENERATED_BODY()

	/** 这一帧的全身姿态。可以为空——空条目仍参与轨迹，只是画不出火柴人。 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Key")
	TObjectPtr<UMotionWeaverPoseAsset> PoseAsset = nullptr;

	/** 相对本 Actor 的位置，cm。 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Key")
	FVector LocalLocation = FVector::ZeroVector;

	/**
	 * 本帧全身朝向，真值。Pitch / Roll 只影响显示，不参与 Root 轨迹求解。
	 * 在编辑器里拖旋转 gizmo 改的就是它。
	 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Key")
	FRotator BodyRotation = FRotator::ZeroRotator;

	/**
	 * BodyRotation.Yaw 的派生缓存（度）。
	 *
	 * 保留它而不是让求解器每次取 BodyRotation.Yaw，是因为求解器在纯数据层
	 * （不依赖 UObject），且已有测试与体检报告都按这个标量写。
	 * 不要直接写这个字段——写 BodyRotation 或调用 SetHeadingDegrees，
	 * 否则两者会失配。
	 */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Key")
	double HeadingDegrees = 0.0;

	/** 链内帧号，从 0 起，按 Settings.FrameRate 换算成秒。必须严格递增。 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Key", meta = (ClampMin = "0"))
	int32 Frame = 0;

	/** 标记这一帧确实停住（速度归零）。不标则曲线连续穿过。 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Key")
	bool bStopHere = false;

	/** 给人看的备注，不参与求解。 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Key")
	FString Label;

	/** 只改偏航（求解器看到的那一份），并同步 BodyRotation 的 Yaw，保留已有 Pitch/Roll。 */
	void SetHeadingDegrees(double InHeadingDegrees)
	{
		HeadingDegrees = InHeadingDegrees;
		BodyRotation.Yaw = InHeadingDegrees;
	}

	/** 改完整朝向（编辑器的旋转 gizmo 走这里），并刷新派生的偏航缓存。 */
	void SetBodyRotation(const FRotator& InBodyRotation)
	{
		BodyRotation = InBodyRotation;
		HeadingDegrees = InBodyRotation.Yaw;
	}

	/**
	 * 从旧数据里恢复一致的朝向。
	 *
	 * BodyRotation 是后加的字段：本字段加入之前存下的关卡里它恒为零旋，
	 * 而 HeadingDegrees 有值。不修的话所有角色会突然集体朝正前方。
	 * 判据取「BodyRotation 零旋 + HeadingDegrees 非零」，两者都非零时以 BodyRotation 为准
	 * （那是用户真的转过了）。
	 */
	void NormalizeRotationConsistency()
	{
		if (BodyRotation.IsNearlyZero(1e-4) && !FMath::IsNearlyZero(HeadingDegrees, 1e-4))
		{
			BodyRotation.Yaw = HeadingDegrees;
		}
	}
};

/** 求解参数。默认值对应当前封盘契约：120 Hz。 */
USTRUCT(BlueprintType)
struct MOTIONWEAVER_API FMotionWeaverChainSettings
{
	GENERATED_BODY()

	/** 帧率。模型契约是 120，别按 30 填。 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Chain", meta = (ClampMin = "1.0"))
	double FrameRate = 120.0;

	/** 水平速度上限，cm/s。超过即判定该段超速。 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Chain", meta = (ClampMin = "1.0"))
	double MaxSpeedCmPerSecond = 400.0;

	/** 转身速率上限，deg/s。超过即判定该段转不过去。 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Chain", meta = (ClampMin = "1.0"))
	double MaxTurnRateDegPerSecond = 720.0;

	/**
	 * 超限时自动插 key 并膨胀该段时长。
	 * 光插 key 不降速——速度 = 距离 / 时间，时间不变就不会变慢。
	 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Chain")
	bool bAutoInsertKeys = true;

	/** 单次求解最多把总时长拉长到几倍。超过就报错，不再拉。 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Chain", meta = (ClampMin = "1.0"))
	double MaxTimeDilation = 2.0;

	/** 插 key 后切线会变，可能又微超。最多迭代几轮。 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Chain", meta = (ClampMin = "1", ClampMax = "32"))
	int32 MaxSolveIterations = 6;

	/**
	 * 朝向改为跟随水平位移方向。
	 * 开启后每个 key 的 HeadingDegrees 不再参与求解。
	 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Chain")
	bool bFollowVelocityHeading = false;

	/** 每隔多少帧画一支方向箭头。 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Chain", meta = (ClampMin = "1"))
	int32 ArrowStrideFrames = 6;

	/** 箭头杆长，cm。 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Chain", meta = (ClampMin = "1.0"))
	double ArrowLengthCm = 20.0;

	/**
	 * 用真蒙皮显示每个关键帧的姿势（资产记录里的 PreviewMesh + PreviewMaterials）。
	 * 关掉或资产没有预览网格时，退回画骨架线。
	 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Chain")
	bool bShowSkinnedPoses = true;

	/** 没有预览网格时是否画骨架线兜底。 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Chain")
	bool bDrawStickFigures = true;

	/** 从视口拖入新 key 时，帧号取「最后一个 key 的帧号 + 这个值」。 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Chain", meta = (ClampMin = "1"))
	int32 NewKeyFrameStride = 24;
};

/** 求解后的一帧轨迹点。 */
USTRUCT(BlueprintType)
struct MOTIONWEAVER_API FMotionWeaverTrajectorySample
{
	GENERATED_BODY()

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Sample")
	int32 Frame = 0;

	/** 相对本 Actor 的位置，cm。 */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Sample")
	FVector LocalLocation = FVector::ZeroVector;

	/** 已展开（unwrapped）的偏航角，连续，可超过 360。 */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Sample")
	double HeadingDegrees = 0.0;

	/** 该帧的水平速度，cm/s。取解析导数，所以停止点精确为 0。 */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Sample")
	double SpeedCmPerSecond = 0.0;

	/** 该帧的偏航角速度，deg/s，带符号。 */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Sample")
	double TurnRateDegPerSecond = 0.0;
};

/** 自动插入的派生 key。不属于创作数据，每次求解重算。 */
USTRUCT(BlueprintType)
struct MOTIONWEAVER_API FMotionWeaverDerivedKey
{
	GENERATED_BODY()

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Derived")
	int32 Frame = 0;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Derived")
	FVector LocalLocation = FVector::ZeroVector;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Derived")
	double HeadingDegrees = 0.0;
};

/** 相邻两个创作关键帧之间的一段。 */
USTRUCT(BlueprintType)
struct MOTIONWEAVER_API FMotionWeaverChainSegment
{
	GENERATED_BODY()

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Segment")
	int32 KeyIndexA = 0;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Segment")
	int32 KeyIndexB = 0;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Segment")
	int32 StartFrame = 0;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Segment")
	int32 FrameCount = 0;

	/** 曲线弧长，cm。与 ChordCm 的比值就是这段有多弯。 */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Segment")
	double LengthCm = 0.0;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Segment")
	double ChordCm = 0.0;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Segment")
	double DurationSeconds = 0.0;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Segment")
	double MeanSpeedCmPerSecond = 0.0;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Segment")
	double PeakSpeedCmPerSecond = 0.0;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Segment")
	double YawDeltaDegrees = 0.0;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Segment")
	double PeakTurnRateDegPerSecond = 0.0;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Segment")
	bool bExceedsMaxSpeed = false;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Segment")
	bool bExceedsMaxTurnRate = false;

	/** 峰值 ÷ 上限，>1 就是超了。 */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Segment")
	double SpeedRatio = 0.0;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Segment")
	double TurnRateRatio = 0.0;

	/** 这段被膨胀到原时长的几倍。1 表示没动。 */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Segment")
	double TimeDilation = 1.0;

	/** 这段自动插了几个派生 key。 */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Segment")
	int32 InsertedKeyCount = 0;
};

/** 一次求解的体检报告。 */
USTRUCT(BlueprintType)
struct MOTIONWEAVER_API FMotionWeaverChainDiagnostics
{
	GENERATED_BODY()

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Diagnostics")
	TArray<FMotionWeaverChainSegment> Segments;

	/** 阻断性问题：有错就没有可用轨迹。 */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Diagnostics")
	TArray<FString> Errors;

	/** 不阻断，但值得看一眼。 */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Diagnostics")
	TArray<FString> Warnings;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Diagnostics")
	int32 FirstFrame = 0;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Diagnostics")
	int32 LastFrame = 0;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Diagnostics")
	int32 SampleCount = 0;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Diagnostics")
	double TotalLengthCm = 0.0;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Diagnostics")
	double TotalYawDegrees = 0.0;

	/** 最终（膨胀后）的全局峰值。 */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Diagnostics")
	double MaxSpeedCmPerSecond = 0.0;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Diagnostics")
	double MaxTurnRateDegPerSecond = 0.0;

	/** 膨胀前的峰值。自动修会把数字变好看，留原值问题才没被藏起来。 */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Diagnostics")
	double OriginalMaxSpeedCmPerSecond = 0.0;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Diagnostics")
	double OriginalMaxTurnRateDegPerSecond = 0.0;

	/** 整条链总时长被拉到原来的几倍。 */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Diagnostics")
	double TotalTimeDilation = 1.0;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Diagnostics")
	int32 DerivedKeyCount = 0;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Diagnostics")
	int32 DilationIterations = 0;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Diagnostics")
	bool bSolved = false;

	bool IsValid() const { return bSolved && Errors.Num() == 0; }

	void Reset()
	{
		Segments.Reset();
		Errors.Reset();
		Warnings.Reset();
		FirstFrame = 0;
		LastFrame = 0;
		SampleCount = 0;
		TotalLengthCm = 0.0;
		TotalYawDegrees = 0.0;
		MaxSpeedCmPerSecond = 0.0;
		MaxTurnRateDegPerSecond = 0.0;
		OriginalMaxSpeedCmPerSecond = 0.0;
		OriginalMaxTurnRateDegPerSecond = 0.0;
		TotalTimeDilation = 1.0;
		DerivedKeyCount = 0;
		DilationIterations = 0;
		bSolved = false;
	}
};

/**
 * 求解结果打包。
 *
 * 做成 USTRUCT 是为了能当 Actor 的只读属性挂在细节面板上——
 * 求解失败时原因要当场看得见，而不是只在日志里。
 */
USTRUCT(BlueprintType)
struct MOTIONWEAVER_API FMotionWeaverChainResult
{
	GENERATED_BODY()

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Result")
	TArray<FMotionWeaverTrajectorySample> Samples;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Result")
	TArray<FMotionWeaverDerivedKey> DerivedKeys;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Result")
	FMotionWeaverChainDiagnostics Diagnostics;

	void Reset()
	{
		Samples.Reset();
		DerivedKeys.Reset();
		Diagnostics.Reset();
	}
};
