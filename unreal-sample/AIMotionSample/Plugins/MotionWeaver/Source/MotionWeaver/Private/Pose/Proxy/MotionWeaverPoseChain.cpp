// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Proxy/MotionWeaverPoseChain.h"

#include "Core/MotionWeaverLog.h"

namespace
{
	/** 判定「已经满足上限」的容差，避免浮点噪声触发无意义的膨胀。 */
	constexpr double DilationEpsilon = 1e-4;

	/** 求解过程中的可变关键帧。位置与偏航永不改动，只有帧号会被膨胀。 */
	struct FKeyState
	{
		FVector Position = FVector::ZeroVector;
		double Heading = 0.0;
		int32 Frame = 0;
		bool bStop = false;
		int32 SourceIndex = INDEX_NONE;
	};

	double HorizontalSpeed(const FVector& Velocity)
	{
		return FMath::Sqrt(Velocity.X * Velocity.X + Velocity.Y * Velocity.Y);
	}

	/**
	 * 每个关键帧处的速度（位置切线）与偏航角速度。
	 *
	 * 内部点用相邻两帧的中心差分，端点用单侧差分；标了 bStopHere 的点切线取零，
	 * 于是速度在该帧精确为 0——这正是「撑住箱子那一下真的停住」的表达方式。
	 */
	void ComputeTangents(
		const TArray<FKeyState>& Keys,
		double FrameRate,
		TArray<FVector>& OutVelocity,
		TArray<double>& OutYawRate)
	{
		const int32 Num = Keys.Num();
		OutVelocity.SetNum(Num);
		OutYawRate.SetNum(Num);

		for (int32 Index = 0; Index < Num; ++Index)
		{
			if (Keys[Index].bStop || Num == 1)
			{
				OutVelocity[Index] = FVector::ZeroVector;
				OutYawRate[Index] = 0.0;
				continue;
			}

			const int32 LeftIndex = (Index == 0) ? 0 : Index - 1;
			const int32 RightIndex = (Index == Num - 1) ? Num - 1 : Index + 1;
			const double DeltaSeconds =
				static_cast<double>(Keys[RightIndex].Frame - Keys[LeftIndex].Frame) / FrameRate;

			if (DeltaSeconds <= 0.0)
			{
				OutVelocity[Index] = FVector::ZeroVector;
				OutYawRate[Index] = 0.0;
				continue;
			}

			OutVelocity[Index] = (Keys[RightIndex].Position - Keys[LeftIndex].Position) / DeltaSeconds;
			OutYawRate[Index] = (Keys[RightIndex].Heading - Keys[LeftIndex].Heading) / DeltaSeconds;
		}
	}

	/** 在 [A, B] 这一段的三次 Hermite 上求值。U 已按时间归一化到 [0, 1]。 */
	void EvaluateSegment(
		const FKeyState& A, const FKeyState& B,
		const FVector& VelocityA, const FVector& VelocityB,
		double YawRateA, double YawRateB,
		double FrameRate,
		double U,
		FVector& OutPosition, FVector& OutVelocity,
		double& OutHeading, double& OutYawRate)
	{
		// Hermite 的切线项是「速度 × 时长」，速度的单位是 cm/s，所以时长必须换算成秒。
		// 直接用帧数会让位移被放大 FrameRate 倍（120 倍），曲线上看就是冲到几千厘米外。
		// 求导得到的 OutVelocity 也要再除回来，才能得到 cm/s。
		const int32 FrameSpan = B.Frame - A.Frame;
		const double Duration = static_cast<double>(FrameSpan) / FrameRate;
		if (Duration <= 0.0)
		{
			OutPosition = A.Position;
			OutVelocity = FVector::ZeroVector;
			OutHeading = A.Heading;
			OutYawRate = 0.0;
			return;
		}

		U = FMath::Clamp(U, 0.0, 1.0);
		const double U2 = U * U;
		const double U3 = U2 * U;

		// 三次 Hermite 基函数：U=0 时 H00=1 其余为 0，U=1 时 H01=1 其余为 0。
		// 曲线因此精确落在两个关键帧上，不靠迭代逼近。
		const double H00 = 2.0 * U3 - 3.0 * U2 + 1.0;
		const double H10 = U3 - 2.0 * U2 + U;
		const double H01 = -2.0 * U3 + 3.0 * U2;
		const double H11 = U3 - U2;

		OutPosition = H00 * A.Position + H10 * Duration * VelocityA
			+ H01 * B.Position + H11 * Duration * VelocityB;

		OutHeading = H00 * A.Heading + H10 * Duration * YawRateA
			+ H01 * B.Heading + H11 * Duration * YawRateB;

		// 基函数的解析导数。用帧间差分估速度会让「停止点」变成「接近 0 而不是 0」。
		const double D00 = 6.0 * U2 - 6.0 * U;
		const double D10 = 3.0 * U2 - 4.0 * U + 1.0;
		const double D01 = -6.0 * U2 + 6.0 * U;
		const double D11 = 3.0 * U2 - 2.0 * U;

		OutVelocity = (D00 * A.Position + D10 * Duration * VelocityA
			+ D01 * B.Position + D11 * Duration * VelocityB) / Duration;

		OutYawRate = (D00 * A.Heading + D10 * Duration * YawRateA
			+ D01 * B.Heading + D11 * Duration * YawRateB) / Duration;
	}

	/** Frame 落在哪一段。落在末帧时返回最后一段的下标。 */
	int32 FindSegment(const TArray<FKeyState>& Keys, int32 Frame)
	{
		const int32 Num = Keys.Num();
		for (int32 Index = 0; Index < Num - 1; ++Index)
		{
			if (Frame < Keys[Index + 1].Frame)
			{
				return Index;
			}
		}
		return Num - 2;
	}
}

double FMotionWeaverPoseChainSolver::WrapDegrees(double InDegrees)
{
	double Result = FMath::Fmod(InDegrees + 180.0, 360.0);
	if (Result < 0.0)
	{
		Result += 360.0;
	}
	return Result - 180.0;
}

double FMotionWeaverPoseChainSolver::UnwrapDegrees(double InDegrees, double InReference)
{
	// 取与参考值最近的等价角：相差整数个 360，且绝对差最小。
	return InReference + WrapDegrees(InDegrees - InReference);
}

bool FMotionWeaverPoseChainSolver::Solve(
	const TArray<FMotionWeaverPoseKey>& Keys,
	const FMotionWeaverChainSettings& Settings,
	FMotionWeaverChainResult& OutResult)
{
	OutResult.Reset();
	FMotionWeaverChainDiagnostics& Diagnostics = OutResult.Diagnostics;

	if (Keys.Num() == 0)
	{
		Diagnostics.Errors.Add(TEXT("姿态链没有任何关键帧。"));
		return false;
	}
	if (Settings.FrameRate <= 0.0)
	{
		Diagnostics.Errors.Add(TEXT("帧率必须为正数。"));
		return false;
	}
	if (Settings.MaxSpeedCmPerSecond <= 0.0 || Settings.MaxTurnRateDegPerSecond <= 0.0)
	{
		Diagnostics.Errors.Add(TEXT("速度与转角上限必须为正数。"));
		return false;
	}

	// 按帧号排序。作者在细节面板里调换顺序不该直接报错，但重排要留下痕迹。
	TArray<FMotionWeaverPoseKey> Sorted = Keys;
	Sorted.StableSort([](const FMotionWeaverPoseKey& A, const FMotionWeaverPoseKey& B)
	{
		return A.Frame < B.Frame;
	});

	for (int32 Index = 0; Index < Keys.Num(); ++Index)
	{
		if (Keys[Index].Frame != Sorted[Index].Frame)
		{
			Diagnostics.Warnings.Add(TEXT("关键帧原本不是按帧号升序的，求解时已重排。"));
			break;
		}
	}

	for (int32 Index = 1; Index < Sorted.Num(); ++Index)
	{
		if (Sorted[Index].Frame == Sorted[Index - 1].Frame)
		{
			Diagnostics.Errors.Add(FString::Printf(
				TEXT("第 %d 帧上有两个关键帧；同一帧不能出现两个姿态。"), Sorted[Index].Frame));
			return false;
		}
	}

	const double FrameRate = Settings.FrameRate;

	// --- 工作关键帧：位置固定、偏航展开成连续标量、帧号可被膨胀改 ---
	TArray<FKeyState> Working;
	Working.Reserve(Sorted.Num());
	for (int32 Index = 0; Index < Sorted.Num(); ++Index)
	{
		const FMotionWeaverPoseKey& Key = Sorted[Index];
		FKeyState& State = Working.AddDefaulted_GetRef();
		State.Position = Key.LocalLocation;
		State.Frame = Key.Frame;
		State.bStop = Key.bStopHere;
		State.SourceIndex = Index;

		// 偏航展开：350° -> 10° 应该只转 20°，而不是倒着转 340°。
		State.Heading = (Index == 0)
			? Key.HeadingDegrees
			: UnwrapDegrees(Key.HeadingDegrees, Working[Index - 1].Heading);

		if (Key.PoseAsset == nullptr)
		{
			Diagnostics.Warnings.Add(FString::Printf(
				TEXT("第 %d 帧没有姿势资产：它只参与轨迹，不提供火柴人。"), Key.Frame));
		}
	}

	const int32 FirstFrame = Working[0].Frame;

	// 单帧链：没有位移、没有转角。
	if (Working.Num() == 1)
	{
		FMotionWeaverTrajectorySample& Sample = OutResult.Samples.AddDefaulted_GetRef();
		Sample.Frame = Working[0].Frame;
		Sample.LocalLocation = Working[0].Position;
		Sample.HeadingDegrees = Working[0].Heading;
		Sample.SpeedCmPerSecond = 0.0;
		Sample.TurnRateDegPerSecond = 0.0;

		Diagnostics.bSolved = true;
		Diagnostics.FirstFrame = Working[0].Frame;
		Diagnostics.LastFrame = Working[0].Frame;
		Diagnostics.SampleCount = 1;
		return true;
	}

	// 初始段长（帧）。后面只增不减。
	TArray<int32> Durations;
	Durations.SetNum(Working.Num() - 1);
	for (int32 Index = 0; Index < Working.Num() - 1; ++Index)
	{
		Durations[Index] = Working[Index + 1].Frame - Working[Index].Frame;
	}
	const TArray<int32> InitialDurations = Durations;

	const int32 OriginalTotalFrames = Working.Last().Frame - Working[0].Frame;
	const double MaxTotalFrames =
		static_cast<double>(OriginalTotalFrames) * FMath::Max(1.0, Settings.MaxTimeDilation);

	TArray<FVector> Velocity;
	TArray<double> YawRate;
	TArray<double> SegmentPeakSpeed;
	TArray<double> SegmentPeakTurn;

	// 由段长重建帧号，然后沿整条链采样。体检与最终输出共用这一份实现，
	// 避免「检查用的曲线」和「输出给下游的曲线」不知不觉分叉。
	auto RebuildAndSample = [&]()
	{
		int32 Cursor = FirstFrame;
		Working[0].Frame = Cursor;
		for (int32 Index = 0; Index < Durations.Num(); ++Index)
		{
			Cursor += Durations[Index];
			Working[Index + 1].Frame = Cursor;
		}

		ComputeTangents(Working, FrameRate, Velocity, YawRate);

		OutResult.Samples.Reset();
		SegmentPeakSpeed.Reset();
		SegmentPeakTurn.Reset();
		SegmentPeakSpeed.SetNumZeroed(Durations.Num());
		SegmentPeakTurn.SetNumZeroed(Durations.Num());

		const int32 LastFrame = Working.Last().Frame;
		for (int32 Frame = FirstFrame; Frame <= LastFrame; ++Frame)
		{
			const int32 SegmentIndex = FindSegment(Working, Frame);
			const FKeyState& A = Working[SegmentIndex];
			const FKeyState& B = Working[SegmentIndex + 1];

			const double U = static_cast<double>(Frame - A.Frame)
				/ static_cast<double>(FMath::Max(1, B.Frame - A.Frame));

			FVector Position;
			FVector Vel;
			double Heading = 0.0;
			double Rate = 0.0;
			EvaluateSegment(A, B, Velocity[SegmentIndex], Velocity[SegmentIndex + 1],
				YawRate[SegmentIndex], YawRate[SegmentIndex + 1], FrameRate, U,
				Position, Vel, Heading, Rate);

			FMotionWeaverTrajectorySample& Sample = OutResult.Samples.AddDefaulted_GetRef();
			Sample.Frame = Frame;
			Sample.LocalLocation = Position;
			Sample.HeadingDegrees = Heading;
			Sample.SpeedCmPerSecond = HorizontalSpeed(Vel);
			Sample.TurnRateDegPerSecond = FMath::Abs(Rate);

			if (SegmentPeakSpeed.IsValidIndex(SegmentIndex))
			{
				SegmentPeakSpeed[SegmentIndex] =
					FMath::Max(SegmentPeakSpeed[SegmentIndex], Sample.SpeedCmPerSecond);
				SegmentPeakTurn[SegmentIndex] =
					FMath::Max(SegmentPeakTurn[SegmentIndex], Sample.TurnRateDegPerSecond);
			}
		}
	};

	bool bHitDilationCap = false;
	int32 Iterations = 0;

	// --- 体检 + 自动修正 ---
	// 修正会改帧号，切线随之变化，所以必须迭代到稳定。
	while (true)
	{
		RebuildAndSample();

		if (!Settings.bAutoInsertKeys)
		{
			break;
		}

		// 拉长倍数：速度与转角都按 1/时长 缩放，取两者较大者。
		bool bNeedsWork = false;
		TArray<double> Required;
		Required.SetNumZeroed(Durations.Num());
		for (int32 Index = 0; Index < Durations.Num(); ++Index)
		{
			const double SpeedNeed = SegmentPeakSpeed[Index] / Settings.MaxSpeedCmPerSecond;
			const double TurnNeed = SegmentPeakTurn[Index] / Settings.MaxTurnRateDegPerSecond;
			Required[Index] = FMath::Max(SpeedNeed, TurnNeed);
			if (Required[Index] > 1.0 + DilationEpsilon)
			{
				bNeedsWork = true;
			}
		}

		if (!bNeedsWork)
		{
			break;
		}
		if (Iterations >= Settings.MaxSolveIterations)
		{
			Diagnostics.Errors.Add(FString::Printf(
				TEXT("迭代 %d 轮后仍有超限段，已停止自动修正。"), Iterations));
			break;
		}

		// 只动超限段，其它段一个帧都不变。
		double ProjectedTotal = 0.0;
		for (int32 Index = 0; Index < Durations.Num(); ++Index)
		{
			const double Factor = FMath::Max(1.0, Required[Index]);
			Durations[Index] = FMath::Max(Durations[Index],
				FMath::RoundToInt(Durations[Index] * Factor));
			ProjectedTotal += Durations[Index];
		}

		// 总时长的硬上限。整条链被拉成几倍时语义已经变了，宁可报错也不硬拉。
		if (ProjectedTotal > MaxTotalFrames)
		{
			const double Scale = MaxTotalFrames / ProjectedTotal;
			for (int32 Index = 0; Index < Durations.Num(); ++Index)
			{
				Durations[Index] = FMath::Max(InitialDurations[Index],
					FMath::RoundToInt(Durations[Index] * Scale));
			}
			bHitDilationCap = true;
			Iterations += 1;
			Diagnostics.Warnings.Add(FString::Printf(
				TEXT("整条链拉长到上限 %.2f 倍（%d 帧）后停止继续拉伸。"),
				FMath::Max(1.0, Settings.MaxTimeDilation), FMath::RoundToInt(MaxTotalFrames)));
			RebuildAndSample();
			break;
		}

		Iterations += 1;
	}

	// --- 装配输出 ---
	Diagnostics.Segments.Reset();
	OutResult.DerivedKeys.Reset();

	double TotalLength = 0.0;
	for (int32 Index = 0; Index < Durations.Num(); ++Index)
	{
		FMotionWeaverChainSegment& Segment = Diagnostics.Segments.AddDefaulted_GetRef();
		const FKeyState& A = Working[Index];
		const FKeyState& B = Working[Index + 1];

		Segment.KeyIndexA = A.SourceIndex;
		Segment.KeyIndexB = B.SourceIndex;
		Segment.StartFrame = A.Frame;
		Segment.FrameCount = B.Frame - A.Frame;
		Segment.DurationSeconds = static_cast<double>(Segment.FrameCount) / FrameRate;
		Segment.ChordCm = FVector::Dist(A.Position, B.Position);
		Segment.YawDeltaDegrees = B.Heading - A.Heading;
		Segment.PeakSpeedCmPerSecond = SegmentPeakSpeed.IsValidIndex(Index) ? SegmentPeakSpeed[Index] : 0.0;
		Segment.PeakTurnRateDegPerSecond = SegmentPeakTurn.IsValidIndex(Index) ? SegmentPeakTurn[Index] : 0.0;

		// 弧长直接累加输出样本，和画面上那条曲线是同一条。
		double Length = 0.0;
		for (int32 SampleIndex = 1; SampleIndex < OutResult.Samples.Num(); ++SampleIndex)
		{
			const FMotionWeaverTrajectorySample& Prev = OutResult.Samples[SampleIndex - 1];
			const FMotionWeaverTrajectorySample& Curr = OutResult.Samples[SampleIndex];
			if (Curr.Frame <= A.Frame || Curr.Frame > B.Frame)
			{
				continue;
			}
			Length += FVector::Dist(Prev.LocalLocation, Curr.LocalLocation);
		}
		Segment.LengthCm = Length;
		TotalLength += Length;

		Segment.MeanSpeedCmPerSecond =
			Segment.DurationSeconds > 0.0 ? (Length / Segment.DurationSeconds) : 0.0;

		const double InitialFrames = static_cast<double>(FMath::Max(1, InitialDurations[Index]));
		Segment.TimeDilation = static_cast<double>(Segment.FrameCount) / InitialFrames;

		Segment.SpeedRatio = Segment.PeakSpeedCmPerSecond / Settings.MaxSpeedCmPerSecond;
		Segment.TurnRateRatio = Segment.PeakTurnRateDegPerSecond / Settings.MaxTurnRateDegPerSecond;
		Segment.bExceedsMaxSpeed = Segment.SpeedRatio > 1.0 + DilationEpsilon;
		Segment.bExceedsMaxTurnRate = Segment.TurnRateRatio > 1.0 + DilationEpsilon;

		// 被膨胀过的段生成派生关键帧：它们是曲线上的采样点，供下游导出与可视化
		// 区分「作者摆的」和「自动插的」。它们不是输入，也不改变曲线——
		// 降速完全来自时长膨胀，插点本身并不减速。
		if (Segment.TimeDilation > 1.0 + DilationEpsilon)
		{
			const int32 InsertCount = FMath::Max(0, FMath::CeilToInt(Segment.TimeDilation) - 1);
			Segment.InsertedKeyCount = InsertCount;
			for (int32 InsertIndex = 1; InsertIndex <= InsertCount; ++InsertIndex)
			{
				const int32 InsertFrame = A.Frame + FMath::RoundToInt(
					static_cast<double>(Segment.FrameCount) * InsertIndex / (InsertCount + 1));
				for (const FMotionWeaverTrajectorySample& Sample : OutResult.Samples)
				{
					if (Sample.Frame == InsertFrame)
					{
						FMotionWeaverDerivedKey& Derived = OutResult.DerivedKeys.AddDefaulted_GetRef();
						Derived.Frame = Sample.Frame;
						Derived.LocalLocation = Sample.LocalLocation;
						Derived.HeadingDegrees = Sample.HeadingDegrees;
						break;
					}
				}
			}
		}

		if (Segment.bExceedsMaxSpeed)
		{
			Diagnostics.Errors.Add(FString::Printf(
				TEXT("第 %d 帧到第 %d 帧峰值 %.0f cm/s（上限 %.0f），这段走得太快。"),
				A.Frame, B.Frame, Segment.PeakSpeedCmPerSecond, Settings.MaxSpeedCmPerSecond));
		}
		if (Segment.bExceedsMaxTurnRate)
		{
			Diagnostics.Errors.Add(FString::Printf(
				TEXT("第 %d 帧到第 %d 帧峰值 %.0f deg/s（上限 %.0f），这段转不过来。"),
				A.Frame, B.Frame, Segment.PeakTurnRateDegPerSecond, Settings.MaxTurnRateDegPerSecond));
		}
	}

	Diagnostics.FirstFrame = Working[0].Frame;
	Diagnostics.LastFrame = Working.Last().Frame;
	Diagnostics.SampleCount = OutResult.Samples.Num();
	Diagnostics.TotalLengthCm = TotalLength;
	Diagnostics.TotalYawDegrees = Working.Last().Heading - Working[0].Heading;
	Diagnostics.DerivedKeyCount = OutResult.DerivedKeys.Num();
	Diagnostics.DilationIterations = Iterations;
	Diagnostics.TotalTimeDilation =
		OriginalTotalFrames > 0
			? static_cast<double>(Working.Last().Frame - Working[0].Frame)
				/ static_cast<double>(OriginalTotalFrames)
			: 1.0;

	double MaxSpeed = 0.0;
	double MaxTurn = 0.0;
	for (const FMotionWeaverTrajectorySample& Sample : OutResult.Samples)
	{
		MaxSpeed = FMath::Max(MaxSpeed, Sample.SpeedCmPerSecond);
		MaxTurn = FMath::Max(MaxTurn, Sample.TurnRateDegPerSecond);
	}
	Diagnostics.MaxSpeedCmPerSecond = MaxSpeed;
	Diagnostics.MaxTurnRateDegPerSecond = MaxTurn;

	// 膨胀前的峰值：段峰值乘以膨胀倍数即还原成原始时长下的速度。
	// 自动修正会把数字变好看，留一份原始值，问题才没被藏起来。
	double OriginalMaxSpeed = 0.0;
	double OriginalMaxTurn = 0.0;
	for (const FMotionWeaverChainSegment& Segment : Diagnostics.Segments)
	{
		OriginalMaxSpeed =
			FMath::Max(OriginalMaxSpeed, Segment.PeakSpeedCmPerSecond * Segment.TimeDilation);
		OriginalMaxTurn =
			FMath::Max(OriginalMaxTurn, Segment.PeakTurnRateDegPerSecond * Segment.TimeDilation);
	}
	Diagnostics.OriginalMaxSpeedCmPerSecond = OriginalMaxSpeed;
	Diagnostics.OriginalMaxTurnRateDegPerSecond = OriginalMaxTurn;

	if (bHitDilationCap && Diagnostics.Errors.Num() == 0)
	{
		Diagnostics.Errors.Add(TEXT("触到时长膨胀上限后仍未能满足约束，这段动作需要重新摆关键帧。"));
	}

	Diagnostics.bSolved = Diagnostics.Errors.Num() == 0;

	if (!Diagnostics.bSolved)
	{
		UE_LOG(LogMotionWeaver, Warning,
			TEXT("姿态链求解未通过：%d 条问题，%d 条提醒。"),
			Diagnostics.Errors.Num(), Diagnostics.Warnings.Num());
	}

	return Diagnostics.bSolved;
}
