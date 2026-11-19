// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"

/**
 * RefPose 模型输出的坐标契约。
 *
 * 全部数学一律用引擎自带的双精度类型（FVector3d / FQuat4d / FMatrix44d /
 * FTransform3d），不自造矩阵：引擎已经处理好四元数分支、正交化与转置语义，
 * 自造版本只会引入「分量放错行」这类难以察觉的错误。
 *
 * 数据语义（用 frozen 数据独立判据确认，见 tools/verify_refpose_ue_contract.py）：
 *  - 位置：米制、Y-up、Root 局部。逐帧骨骼位置；除 Root→Hips 外，
 *    全部 76 根骨的「与父骨距离」标准差 ≤ 4.6e-8 m，即刚性。
 *  - 参考偏移：skeleton.json 的 `position` 是父级相对平移，其长度与逐帧
 *    姿态的骨长逐骨相等（Hips 除外）。因此它可以直接作为 UE 参考骨长。
 *  - Root→Hips 不是刚性链：Root 是独立辅助节点（位置随片段移动），
 *    Hips 才是人体根。UE 侧必须把 Root 位移交给 Root 轨道，
 *    不能把它当作 Hips 的父级骨长。
 *  - 旋转：3x3 旋转，6D 按 row-major 交错（m00,m01,m10,m11,m20,m21）；
 *    Root 骨旋转恒为单位矩阵，说明它已去掉 Root 朝向。
 *  - 归一化只作用于位置通道。
 *
 * U1（未证实，禁止依赖）：旋转与位置的骨轴是否同源尚未闭合。
 * 因此本类型不用旋转做 FK 重建位置，也不用位置反推旋转；二者分开解码。
 */
struct MOTIONWEAVER_API FRefPosePoseContract
{
	static constexpr int32 PositionStride = 3;
	static constexpr int32 RotationStride = 6;
	static constexpr int32 BoneStride = PositionStride + RotationStride;

	struct FDecodedFrame
	{
		/** 每根骨的米制 Y-up 位置（Root 局部）。 */
		TArray<FVector3d> PositionsMeters;

		/** 每根骨的旋转（米制 Y-up）。仅供诊断，不参与骨长解算。 */
		TArray<FQuat4d> Rotations;
	};

	/**
	 * 解码一段窗口的模型输出。
	 *
	 * @param Raw    原始输出，长度 = FrameCount * BoneCount * 9。
	 * @param Mean   位置均值，长度 = BoneCount * 3。
	 * @param Std    位置标准差，长度 = BoneCount * 3。
	 */
	static bool DecodeWindow(
		TConstArrayView<float> Raw,
		int32 BoneCount,
		int32 FrameCount,
		TConstArrayView<float> Mean,
		TConstArrayView<float> Std,
		TArray<FDecodedFrame>& OutFrames,
		FString& OutError);

	/**
	 * 按 manifest 声明解 6D 到四元数。
	 *
	 * 前两列直接取自交错值，第三列由叉乘补出——与 manifest 的
	 * `decode.col2 = "cross(col0, col1)"` 一致。不做 Gram–Schmidt：
	 * 真实数据两半范数在 1.0±2e-7 内，额外归一化只会掩盖上游错误。
	 */
	static FQuat4d Decode6D(TConstArrayView<float> Values);

	/** 与 Decode6D 互逆的编码，供把 UE 当前姿态写回模型输入。 */
	static void Encode6D(const FQuat4d& Rotation, float* OutValues);

	/** 米制 Y-up → UE 厘米。 */
	static FVector ToUePosition(const FVector3d& PositionMeters);

	/** UE 厘米 → 米制 Y-up。 */
	static FVector3d ToMotionPosition(const FVector& PositionUe);

	/** 米制 Y-up 旋转 → UE 旋转。 */
	static FQuat4d ToUeRotation(const FQuat4d& Rotation);

	/** UE 旋转 → 米制 Y-up 旋转。 */
	static FQuat4d ToMotionRotation(const FQuat4d& Rotation);

	/**
	 * 把 From 转到 To 的最短弧，180° 反向时用确定性轴。
	 *
	 * 模型旋转通道与位置通道共用同一套骨轴约定（见 RotationAxis），
	 * 因此「位置差分方向 → 旋转」这一步必须与契约层使用同一个最短弧实现，
	 * 否则同一姿态在 UE 侧和解码侧会得到不同的四元数分支。
	 */
	static FQuat4d SwingBetween(const FVector3d& From, const FVector3d& To);
};

/**
 * 把「每帧骨骼位置」解成 UE 可用的逐骨局部变换。
 *
 * 关键约束（必须理解，否则会做出看起来能跑但骨长错误的东西）：
 *  - UE 的 PoseableMesh 要的是相对父骨的局部变换，长度必须来自 UE 骨架的
 *    参考骨长，不能直接写模型的米制绝对偏移。
 *  - 模型的旋转与位置骨轴未闭合（U1），所以方向从位置差分得到，
 *    长度取参考骨长。这样重建出的位置与模型输出方向一致、骨长恒定。
 *  - 朝向用「父级当前姿态携带的静止骨帧 + 最短弧摆动」求得：基准只依赖
 *    当前父级与固定参考，不依赖上一帧或窗口起点。逐窗口重播、硬参考覆盖
 *    都不会因为重新播种而翻 180°。
 *
 * 输出可用 RebuildSkeletonPositions 精确复算，这是本求解器的自检闭环。
 */
struct MOTIONWEAVER_API FRefPoseSkeletonSolver
{
	struct FMetrics
	{
		/** 相对参考骨长的最大偏差（厘米）。由构造方式应恒为 0。 */
		double MaxBoneLengthDeviationCm = 0.0;

		/** 实际使用的最小/最大骨长（厘米），用于发现零长骨。 */
		double MinBoneLengthCm = TNumericLimits<double>::Max();
		double MaxBoneLengthCm = 0.0;

		/** 位置差分退化、沿用参考旋转的骨数。 */
		int32 DegenerateBones = 0;

		/** 父索引非法（越界或非父先子后）的骨数。 */
		int32 InvalidParents = 0;
	};

	/**
	 * @param Parents  父索引（父先子后，根为 -1）。
	 * @param RefLocal 每根骨的参考局部变换：**旋转**是 UE 参考骨架的局部旋转
	 *                （决定骨轴），**平移长度**是模型契约骨长。求解器只用
	 *                平移的模长，方向一律由 UE 旋转的 +X 轴给出。
	 * @param Decoded  逐帧位置与旋转（米制 Y-up、Root 局部）。
	 * @param OutLocal [FrameCount][BoneCount] 的 UE 局部变换。
	 * @param ChildUsable 可选：子骨能否作为「朝向锚点」。空表示全部可用。
	 *                目标骨架里不存在的骨（模型 `*End`、面部）位置上不可见，
	 *                模型对它们的预测也不受任何绑定约束；让它们决定父骨朝向，
	 *                会出现整根手指/头/脚掌翻 180° 的假动作。传 false 后，
	 *                这类父骨退回「父级携带的静止朝向」。
	 */
	static bool Solve(
		TConstArrayView<int32> Parents,
		TConstArrayView<FTransform> RefLocal,
		TConstArrayView<FRefPosePoseContract::FDecodedFrame> Decoded,
		TArray<TArray<FTransform>>& OutLocal,
		FMetrics& OutMetrics,
		FString& OutError,
		TConstArrayView<bool> ChildUsable = TConstArrayView<bool>());

	/**
	 * 用局部变换复算每帧每骨的骨架空间位置（厘米），用于闭环自检。
	 * 返回 [FrameCount][BoneCount]。
	 */
	static bool RebuildSkeletonPositions(
		TConstArrayView<int32> Parents,
		TConstArrayView<TArray<FTransform>> Local,
		TArray<TArray<FVector>>& OutPositionsCm,
		FString& OutError);
};
