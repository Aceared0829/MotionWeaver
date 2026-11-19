// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "RefPose/RefPosePoseContract.h"
#include "RefPose/RefPoseSkeletonMap.h"
#include "RefPose/RefPoseWindowBuilder.h"

class URefPoseModelAsset;
class FRefPoseSession;
struct FPoseFrameSnapshot;

/** 一次生成的诊断指标；与视觉结果分开报告，避免用「看起来能播」代替数值。 */
struct MOTIONWEAVER_API FRefPoseGenerationMetrics
{
	/** 窗口数。 */
	int32 WindowCount = 0;
	/** 成功解算的帧数。 */
	int32 FrameCount = 0;
	/** 推理总耗时（毫秒）。 */
	double TotalInferenceMs = 0.0;
	double MaxInferenceMs = 0.0;
	/** 逐窗口推理耗时（毫秒），顺序与实际执行窗口一致。 */
	TArray<double> WindowInferenceMs;
	/** 骨长偏差最大值（厘米）；由求解器构造方式应恒为 0。 */
	double MaxBoneLengthDeviationCm = 0.0;
	/** 位置差分退化、沿用参考旋转的骨数累计。 */
	int32 DegenerateBones = 0;

	/** 被硬参考覆盖的帧数。 */
	int32 HardReferenceFrames = 0;
	/** 硬参考在窗口内的 offset（档位决定），供逐帧标注。 */
	TArray<int32> ReferenceOffsets;
	/** 一个未来窗口的帧数（窗口步长），逐帧判定窗口/参考用。 */
	int32 WindowStride = 0;

	/**
	 * 相邻生成帧 Root 平移差的最大值（厘米）。
	 *
	 * 这是接缝体检，不是精度指标：窗口重复叠加原点、起步样本错位都会
	 * 在这里放大。它只描述输出是否连续，不能反过来证明姿态正确。
	 */
	double MaxRootSeamJumpCm = 0.0;

	/**
	 * 相邻生成帧之间最大的单骨「骨架空间」旋转跳变（度）。
	 *
	 * 刻意不用局部旋转：局部跳变会把「父骨转了、子骨反向补偿以保持朝向」
	 * 也算成抽搐（实测局部 161°/帧时，骨轴在骨架空间里只动了 0.2°）。
	 * 蒙皮看到的是骨架空间旋转，绕骨轴的自转（roll）也在其中，因此这个
	 * 指标才对应「糖果纸拧转」这类可见畸形。
	 */
	double MaxGlobalRotationStepDeg = 0.0;

	/**
	 * 相邻生成帧之间最大的单骨骨架空间位置跳变（厘米）。
	 *
	 * 旋转跳变对「绕骨轴的自转」不敏感，位置跳变则直接对应播放时的抽搐。
	 * 两者一起看，才能区分「骨轴翻转」与「整段姿态被窗口重置」。
	 */
	double MaxPoseStepCm = 0.0;

	/**
	 * 位置跳变最大的骨名与旋转跳变最大的骨名。
	 *
	 * 只报告数值不够用：作者需要知道「是哪根骨在跳」，才能判断是模型
	 * 位置错、映射错，还是硬参考覆盖造成的单帧弹跳。
	 */
	FString MaxPoseStepBone;
	FString MaxGlobalRotationStepBone;

	/**
	 * 位置/旋转跳变指标排除了哪些骨。
	 *
	 * 只统计真正参与蒙皮的骨：UE5 骨架里的 `VB *`、`ik_*`、`prop*`、`poi`、
	 * `attach`、`weapon_*` 离手/脚很远又不参与蒙皮，它们的弧长会把指标
	 * 放大一个数量级（同一段结果实测含辅助骨 159 cm，只算蒙皮骨 74 cm），
	 * 让作者误判。
	 */
	TArray<FString> SeamMetricExcludedBones;

	/**
	 * 模型原始输出（硬参考覆盖之前）与作者参考姿态的逐骨位置差，厘米。
	 *
	 * 这是「模型跟不跟得上关键帧」最直接的数值：输入装配错误（坐标轴、
	 * 旋转先验、骨长）会把它推到几十厘米甚至米级；模型本身的泛化误差
	 * 通常在几厘米。硬参考帧会被作者姿态覆盖，所以这里刻意在覆盖之前统计。
	 */
	double MeanPredictionErrorCm = 0.0;
	double MaxPredictionErrorCm = 0.0;

	/**
	 * 从作者关键帧迁移过来的扭转角（度）。
	 *
	 * 位置通道完全看不见绕骨轴的自转：前臂旋前 180° 时肘、腕关节位置
	 * 可以一模一样，蒙皮却会拧成一团。这里统计迁移量的绝对值，
	 * 用来判断「畸形是不是扭转缺失造成的」。
	 */
	double MeanAuthoredTwistDeg = 0.0;
	double MaxAuthoredTwistDeg = 0.0;

	/** 求解器对整段做的时长膨胀倍数（1 表示未膨胀）。 */
	double TotalTimeDilation = 1.0;

	/** 姿态组帧率与模型契约帧率，用于提示「播放速度按哪一份」。 */
	double GroupFrameRate = 0.0;
	double ModelFrameRate = 0.0;

	/** 第一个生成帧对应的求解样本帧号。 */
	int32 StartSampleFrame = INDEX_NONE;
	/** 播放原点（第一段 history 末帧）的求解样本帧号。 */
	int32 PlaybackOriginFrame = INDEX_NONE;
};

/**
 * 单窗口生成：装配 → 推理 → 解码 → 解算局部变换。
 *
 * 单窗口是整条链里可独立复算的一步：坐标与骨长错误会在这里暴露。
 * 整段动作的多窗口调度由 `FRefPoseGroupGenerator` 负责；它复用同一个
 * `FRefPoseSession`，逐窗口调用这里的 GenerateWindow。
 */
struct MOTIONWEAVER_API FRefPoseGenerator
{
	/** 一帧输入姿态（米制 Y-up、Root 局部）。 */
	struct FPoseInput
	{
		TArray<FVector3d> PositionsMeters;
		/** 长度 = BoneCount * 6，row-major 交错。 */
		TArray<float> Rotations6D;
	};

	/** 一帧生成结果。 */
	struct FFrames
	{
		/** 每帧每骨的 UE 局部变换（相对父骨）。 */
		TArray<TArray<FTransform>> Local;

		/**
		 * 解码后的模型姿态（米制 Y-up、Root 局部）。
		 *
		 * 保留它是为了诊断：编辑器可以按帧对照「模型输出」与「作者参考」。
		 * 多窗口历史刻意**不用**它回灌——回灌会把上一窗口的误差当成事实，
		 * 实测 15 个窗口内位姿误差从 0.08 m 累积到 0.72 m。
		 */
		TArray<FRefPosePoseContract::FDecodedFrame> Decoded;

		/**
		 * 模型输出的原始 6D 旋转（逐帧逐骨，长度 = 帧数*骨数*6）。
		 *
		 * 当前只用于诊断。若将来恢复「生成结果回灌」的历史策略，必须原样
		 * 用它而不是把 Decoded.Rotations 解码再编码：预测的 6D 两列并非
		 * 严格正交（实测 |col0| 最低 0.056），解码再编码会把历史通道改写。
		 */
		TArray<float> RawRotations6D;

		/**
		 * 每帧「模型原始输出 vs 作者参考」的逐骨平均位置差（厘米）。
		 *
		 * 在硬参考覆盖之前统计，因此硬参考帧也能看出模型的原始预测是否跑偏。
		 * 编辑器用它做逐帧调试；硬参考帧仍会按训练契约显示作者姿态。
		 */
		TArray<double> FramePredictionErrorCm;
	};

	/**
	 * 用给定窗口生成 FutureFrames 帧。
	 *
	 * @param Model      已加载契约的模型资产。
	 * @param History    24 帧历史姿态。
	 * @param HistoryRoot 24 帧历史 Root（与姿态同源）。
	 * @param Future     24 帧目标姿态，仅用于提供 reference 与对照 target。
	 * @param RootOrigin 最后一帧历史 Root。
	 * @param FutureRoot 24 帧未来 Root（模型条件输入）。
	 * @param Offsets    已验证参考档位。
	 * @param RefLocal   UE 骨架的参考局部变换（骨长真值）。
	 */
	static bool GenerateWindow(
		const URefPoseModelAsset& Model,
		TConstArrayView<FPoseInput> History,
		TConstArrayView<FRefPoseWindowBuilder::FRootSample> HistoryRoot,
		TConstArrayView<FPoseInput> Future,
		const FRefPoseWindowBuilder::FRootSample& RootOrigin,
		TConstArrayView<FRefPoseWindowBuilder::FRootSample> FutureRoot,
		TConstArrayView<int32> ReferenceOffsets,
		TConstArrayView<FTransform> RefLocal,
		FFrames& OutFrames,
		FRefPoseGenerationMetrics& OutMetrics,
		FString& OutError,
		TConstArrayView<bool> ChildUsable = TConstArrayView<bool>());

	/**
	 * 复用同一个 NNE 会话生成一个窗口。
	 *
	 * 多窗口连续生成时不能每个窗口都重新 Create 会话：那会把 ONNX 反序列化
	 * 与 NNE 实例创建重复十几遍。会话由调用方持有，窗口之间只换输入。
	 */
	static bool GenerateWindow(
		const URefPoseModelAsset& Model,
		FRefPoseSession& Session,
		TConstArrayView<FPoseInput> History,
		TConstArrayView<FRefPoseWindowBuilder::FRootSample> HistoryRoot,
		TConstArrayView<FPoseInput> Future,
		const FRefPoseWindowBuilder::FRootSample& RootOrigin,
		TConstArrayView<FRefPoseWindowBuilder::FRootSample> FutureRoot,
		TConstArrayView<int32> ReferenceOffsets,
		TConstArrayView<FTransform> RefLocal,
		FFrames& OutFrames,
		FRefPoseGenerationMetrics& OutMetrics,
		FString& OutError,
		TConstArrayView<bool> ChildUsable = TConstArrayView<bool>());

	/**
	 * 构造求解器用的模型参考变换。
	 *
	 * 平移长度仍取模型契约（重定向阶段会换成 UE 骨长）；旋转取 UE 参考骨架
	 * 相对最近映射祖先的旋转。这样求解器在静止姿态下能复现 UE 的参考扭转，
	 * 而不是把每根骨都从 +X 用最短弧转过去。若这里继续用单位旋转，
	 * 肩、腕、踝这些有明显静止朝向的骨会在播放时出现不自然的扭转。
	 */
	static bool BuildSolverReference(
		const URefPoseModelAsset& Model,
		const FRefPoseSkeletonMap& Map,
		TConstArrayView<FTransform> SkeletonReference,
		TConstArrayView<int32> SkeletonParents,
		TArray<FTransform>& OutReference,
		FString& OutError);

	/**
	 * 把 UE 参考骨架上的局部姿态转成模型输入。
	 *
	 * 位置先重建到组件空间，去掉 UE 骨架根骨（root motion 由姿态组轨迹负责），
	 * 再转到该帧 Root 轨道的局部帧；姿态输入里的 Root 旋转为 identity
	 * （姿态已经表达在 Root 局部），Root 轨迹本身的朝向由 root_plan 提供。
	 * 模型有、UE 没有的骨（面部、末端）用 skeleton.json 的参考偏移挂到
	 * 最近的已映射祖先上，避免被当成零位移。
	 *
	 * @param bEncodeReferenceRotations
	 *        该姿态是以 reference（硬参考）身份进入模型时为 true：旋转通道用
	 *        「位置差分方向 + 每骨常量骨轴」编码，让参考帧携带朝向信息。
	 *        历史帧为 false：旋转通道取训练分布先验，避免 UE 骨轴与 SOMA
	 *        骨轴不同源（U1）把模型输入推出分布。
	 */
	static bool SkeletonPoseToPoseInput(
		const URefPoseModelAsset& Model,
		const FRefPoseSkeletonMap& Map,
		TConstArrayView<FTransform> SkeletonLocal,
		TConstArrayView<int32> SkeletonParents,
		const FRefPoseWindowBuilder::FRootSample& RootSample,
		bool bEncodeReferenceRotations,
		FPoseInput& OutPose,
		FString& OutError);

	/**
	 * 按骨名把关键帧快照解析到 UE 参考骨架槽位。
	 *
	 * 快照的槽位顺序不一定等于参考骨架；按名字查，查不到就报错，
	 * 不按数组下标硬对。参考骨架里有、快照里没有的骨保持参考局部变换。
	 */
	static bool SnapshotToSkeletonLocal(
		const FPoseFrameSnapshot& Snapshot,
		TConstArrayView<FName> SkeletonBoneNames,
		TConstArrayView<FTransform> SkeletonReference,
		TArray<FTransform>& OutLocal,
		FString& OutError);

	/**
	 * 两个关键帧之间的局部姿态插值（位置 lerp、旋转 slerp）。
	 *
	 * 只用于给模型提供 history/reference 的已有姿态；最终播放帧仍由模型输出覆盖。
	 */
	static bool BlendSkeletonLocal(
		TConstArrayView<FTransform> A,
		TConstArrayView<FTransform> B,
		double Alpha,
		TArray<FTransform>& OutLocal,
		FString& OutError);

	/**
	 * 把作者姿态的「局部扭转」迁移到生成姿态。
	 *
	 * 模型只给位置：骨轴方向（最短弧）唯一，但绕骨轴的 roll 在位置上完全
	 * 不可见——前臂旋前、手腕翻转、头朝向丢了也不会移动任何关节。结果是
	 * 关节位置正确、蒙皮却被拧成麻花。训练旋转通道与 UE 骨轴尚未同源
	 * （U1），不能直接用；作者关键帧本身就是 UE 局部旋转，用它补这份
	 * 欠定信息最可靠。
	 *
	 * 做法分三步，任何一步都不能省：
	 *  1. 先用骨盆（模型 Hips）把作者姿态对齐到生成姿态的身体坐标系。
	 *     两侧 Root 朝向不同（轨迹偏航 vs 快照朝向），直接比世界旋转会把
	 *     整段朝向差当成 roll 灌进去。
	 *  2. 用最短弧把作者的骨轴搬到生成的骨轴上，作者的 roll 随之平行搬运；
	 *     骨轴与摆动（swing）仍由模型位置决定，姿态不会被作者数据改写。
	 *     两根骨轴夹角过大时按权重平滑退回模型自己的 roll，避免在阈值附近
	 *     反复翻面。
	 *  3. 用**迁移前**的骨架空间位置重建每根骨的局部平移。分支骨（骨盆、
	 *     胸、手）的子骨偏移不在骨轴上，绕轴旋转会移动子骨；这里把平移
	 *     反算回去，保证所有关节位置逐帧严格不变。
	 *
	 * Root 的世界旋转保持轨迹给出的值；Hips 由第 1 步对齐，扭转量恒为 0。
	 * 未映射骨不改旋转（它们本来就是 UE 参考姿态），只随父级继承 roll。
	 *
	 * @param Generated [帧][UE 槽位] 生成局部变换（原地修改）。
	 * @param Author    [帧][UE 槽位] 作者局部变换，与 Generated 一一对应。
	 * @param SkeletonParents UE 父索引（父先子后，根为 -1）。
	 * @param SkeletonToModel UE 槽位 → 模型骨；INDEX_NONE 表示未映射。
	 * @param OutMeanTwistDeg/OutMaxTwistDeg 迁移角度的绝对值统计。
	 */
	static bool TransferAuthoredTwist(
		TArray<TArray<FTransform>>& Generated,
		TConstArrayView<TArray<FTransform>> Author,
		TConstArrayView<int32> SkeletonParents,
		TConstArrayView<int32> SkeletonToModel,
		double& OutMeanTwistDeg,
		double& OutMaxTwistDeg,
		FString& OutError);
};
