// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"

#include "RefPoseGenerationSettings.generated.h"

/**
 * 参考姿态档位。
 *
 * 训练侧 `data/runtime/refpose_windows.py` 的默认采样只会产出这四档；
 * 其余稀疏组合属于未验证档位，因此界面只开放这四种，不提供任意帧手选。
 * 想开放任意帧，必须先用 diverse 采样在 test 分区验证，而不是先改 UI。
 */
UENUM(BlueprintType)
enum class ERefPoseReferencePattern : uint8
{
	/** 窗口内没有硬参考，全部由模型自行补全。 */
	None UMETA(DisplayName = "无参考"),

	/** 窗口中间一帧（offset 12）作为硬参考。 */
	Middle UMETA(DisplayName = "中间 (12)"),

	/** 窗口末端一帧（offset 23）作为硬参考。 */
	End UMETA(DisplayName = "末端 (23)"),

	/** 中间 + 末端（offset 12、23），训练默认档位。 */
	MiddleAndEnd UMETA(DisplayName = "中间+末端 (12,23)"),
};

/**
 * 姿态代理组的生成设置。
 *
 * 这些参数只影响「生成并播放」，不参与链式求解本身：
 * 起点决定从哪一段求解样本开始滑窗，参考档位决定模型看到多少硬参考，
 * 模型包路径决定用哪份权重。把它们存在资产上而不是编辑器临时状态里，
 * 是为了让同一次创作在重开编辑器后仍按同一组参数复现。
 */
USTRUCT(BlueprintType)
struct MOTIONWEAVER_API FRefPoseGenerationSettings
{
	GENERATED_BODY()

	/** 参考姿态档位；只有训练验证过的四档可选。 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "RefPose 生成",
		meta = (DisplayName = "参考姿态档位"))
	ERefPoseReferencePattern ReferencePattern = ERefPoseReferencePattern::MiddleAndEnd;

	/**
	 * 从第几个作者关键帧开始生成（0 起）。
	 *
	 *  - 索引 0 表示动作起点：模型仍需要 24 帧前置历史，因此输出从第 25 个
	 *    求解样本开始（与首版整段滑窗一致）。
	 *  - 索引 > 0 表示以该关键帧对应的求解样本作为第一个生成帧：它之前必须
	 *    已有至少 24 帧求解样本，不够时明确报错，不会静默退回到 0。
	 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "RefPose 生成",
		meta = (DisplayName = "起点关键帧", ClampMin = "0"))
	int32 StartKeyIndex = 0;

	/**
	 * 模型包目录（含 model.onnx / skeleton.json / stats.bin / manifest.json）。
	 *
	 * 留空时回退到环境变量 `MW_REFPOSE_UE_PACKAGE`，再回退到
	 * `Saved/MotionWeaver/RefPosePackage.txt`。资产里显式填写后不会静默回退：
	 * 路径不存在就报错，避免「以为换了包、其实还在跑旧权重」。
	 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "RefPose 生成",
		meta = (DisplayName = "模型包目录"))
	FString ModelPackagePath;

	/**
	 * 播放时钟是否改用姿态组帧率。
	 *
	 * 默认关闭：生成的逐帧数据是模型契约帧率（120 fps）下的时间轴，
	 * 用组帧率播放会改变动作速度。只有作者明确想要「按创作帧率预览」时才开。
	 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "RefPose 生成",
		meta = (DisplayName = "按姿态组帧率播放"))
	bool bUseGroupFrameRateForPlayback = false;

	/**
	 * 是否把作者关键帧的「扭转（roll）」迁移到生成姿态。
	 *
	 * 模型只输出逐骨位置，骨轴方向由位置差分唯一确定，但绕骨轴的自转
	 * （前臂旋前、手腕翻转、头朝向）在位置上完全不可见：关节仍然落在
	 * 同一点，蒙皮却会被拧成一团。训练数据的旋转通道与 UE 骨轴尚未同源
	 * （U1），不能直接拿来用；作者关键帧本身就是 UE 局部旋转，是这份
	 * 欠定信息最可靠的来源。
	 *
	 * 这里搬运作者的 roll，但骨轴、位置与骨长仍全部来自模型输出。
	 * 迁移通过绕当前骨轴的旋转完成，所以任何关节的位置都不会改变；
	 * 关闭后回到「父级携带的静止扭转」，可用于对照排查。
	 */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "RefPose 生成",
		meta = (DisplayName = "迁移关键帧扭转"))
	bool bTransferAuthoredTwist = true;

	/**
	 * 把档位换成窗口内的参考 offset 列表。
	 *
	 * offset 是「未来窗口内的帧下标」，由 48 帧窗口契约固定为 12 / 23。
	 * 结果仍要交给 FRefPoseWindowBuilder 做白名单校验，这里只做档位映射。
	 */
	bool ToReferenceOffsets(TArray<int32>& OutOffsets, FString& OutError) const
	{
		OutOffsets.Reset();
		switch (ReferencePattern)
		{
		case ERefPoseReferencePattern::None:
			break;
		case ERefPoseReferencePattern::Middle:
			OutOffsets.Add(12);
			break;
		case ERefPoseReferencePattern::End:
			OutOffsets.Add(23);
			break;
		case ERefPoseReferencePattern::MiddleAndEnd:
			OutOffsets.Add(12);
			OutOffsets.Add(23);
			break;
		default:
			OutError = TEXT("未知的参考姿态档位。");
			return false;
		}
		return true;
	}
};
