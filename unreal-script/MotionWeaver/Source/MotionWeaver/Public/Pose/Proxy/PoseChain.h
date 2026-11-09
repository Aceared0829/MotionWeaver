// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "Pose/Proxy/PoseChainTypes.h"

/**
 * 「若干有序姿态 -> 稠密地面轨迹」的求解器。
 *
 * 只吃数组、只吐数组，不碰 UWorld，因此自动化测试可以直接调用。
 *
 * 曲线形状：时间感知的三次 Hermite。
 *  - 每个关键帧的切线取相邻两帧的速度（端点用单侧差分），
 *    因此整条曲线 C1 连续，并且在 u=0 / u=1 时基函数精确取 0 与 1，
 *    **必然通过每一个关键帧**。这一条不是美观要求：
 *    同一帧还带着全身姿态作为模型硬参考，root 偏 20 cm 就是自相矛盾的输入。
 *  - 偏航先展开成连续标量，再用同一套 Hermite，避免 ±180 处翻转。
 *
 * 体检与修正：
 *  - 逐帧算解析导数得到速度与转角，超限即记录。
 *  - 超限时**只插 key 不降速**（速度 = 距离 / 时间，时间不变就不会变慢）。
 *    所以自动修正必须同时膨胀该段时长，后面所有关键帧整体平移。
 *  - 报告里同时保留膨胀前峰值，避免自动修正把问题藏起来。
 *
 * 数据结构全部放在 PoseChainTypes.h：UHT 要求每个含 GENERATED_BODY
 * 的类型独占一个 .generated.h，两个头文件各自声明同名 USTRUCT 会直接编译失败。
 */
struct MOTIONWEAVER_API FPoseChainSolver
{
	/**
	 * 求解。
	 * @param Keys      创作关键帧。顺序任意，内部按 Frame 排序；Frame 不可重复。
	 * @param Settings  帧率与各项上限。
	 * @param OutResult 逐帧轨迹、派生关键帧与体检报告。
	 * @return 是否得到可用轨迹（Diagnostics.IsValid()）。
	 */
	static bool Solve(
		const TArray<FPoseKey>& Keys,
		const FChainSettings& Settings,
		FChainResult& OutResult);

	/** 折进 [-180, 180)。 */
	static double WrapDegrees(double InDegrees);

	/** 展开成连续标量：返回值与 InDegrees 相差整数个 360，且最接近 InReference。 */
	static double UnwrapDegrees(double InDegrees, double InReference);
};
