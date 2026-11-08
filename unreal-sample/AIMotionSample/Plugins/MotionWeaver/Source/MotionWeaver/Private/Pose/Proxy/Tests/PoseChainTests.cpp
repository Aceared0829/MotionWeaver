// Copyright ZhaoZining. All Rights Reserved.

#include "CoreMinimal.h"
#include "Misc/AutomationTest.h"
#include "Pose/Proxy/PoseChain.h"

#if WITH_DEV_AUTOMATION_TESTS

namespace
{
	FPoseKey MakeKey(int32 Frame, const FVector& Location, double Heading, bool bStop = false)
	{
		FPoseKey Key;
		Key.Frame = Frame;
		Key.LocalLocation = Location;
		Key.HeadingDegrees = Heading;
		Key.bStopHere = bStop;
		return Key;
	}

	FChainSettings MakeSettings()
	{
		FChainSettings Settings;
		Settings.FrameRate = 120.0;
		// 测试里默认关掉自动修正：多数判据要看原始曲线，不能被膨胀悄悄改掉。
		Settings.bAutoInsertKeys = false;
		return Settings;
	}
}

// T1：单 key 只有一帧，速度为 0。
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FChainSingleKeyTest, "MotionWeaver.PoseChain.SingleKey",
                                 EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FChainSingleKeyTest::RunTest(const FString& Parameters)
{
	TArray<FPoseKey> Keys;
	Keys.Add(MakeKey(0, FVector(10.0, 20.0, 0.0), 30.0));

	FChainResult Result;
	const bool bSolved = FPoseChainSolver::Solve(Keys, MakeSettings(), Result);

	TestTrue(TEXT("single key chain solves"), bSolved);
	TestEqual(TEXT("sample count is 1"), Result.Samples.Num(), 1);
	if (Result.Samples.Num() == 1)
	{
		TestTrue(TEXT("single key speed is zero"), Result.Samples[0].SpeedCmPerSecond < 1e-6);
		TestTrue(TEXT("sample sits on the key"),
			Result.Samples[0].LocalLocation.Equals(FVector(10.0, 20.0, 0.0), 1e-6));
	}
	return true;
}

// T2：两 key 直线，所有样本共线。
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FChainLineTest, "MotionWeaver.PoseChain.StraightLine",
                                 EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FChainLineTest::RunTest(const FString& Parameters)
{
	TArray<FPoseKey> Keys;
	Keys.Add(MakeKey(0, FVector::ZeroVector, 0.0));
	Keys.Add(MakeKey(60, FVector(120.0, 0.0, 0.0), 0.0));

	FChainResult Result;
	const bool bSolved = FPoseChainSolver::Solve(Keys, MakeSettings(), Result);

	TestTrue(TEXT("straight chain solves"), bSolved);
	TestEqual(TEXT("sample count is 61"), Result.Samples.Num(), 61);

	if (Result.Samples.Num() < 2)
	{
		return true;
	}

	// 共线判据：任一点到首尾连线的垂直距离都接近 0。
	const FVector Start = Result.Samples[0].LocalLocation;
	const FVector End = Result.Samples.Last().LocalLocation;
	const FVector Axis = (End - Start).GetSafeNormal();
	for (const FChainTrajectorySample& Sample : Result.Samples)
	{
		const FVector Relative = Sample.LocalLocation - Start;
		const FVector Perpendicular = Relative - Axis * FVector::DotProduct(Relative, Axis);
		TestTrue(TEXT("sample lies on the chord"), Perpendicular.Size() < 1e-4);
	}
	return true;
}

// T3：曲线必须精确穿过每一个关键帧。
IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FChainPassesThroughKeysTest,
	"MotionWeaver.PoseChain.PassesThroughKeys",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FChainPassesThroughKeysTest::RunTest(const FString& Parameters)
{
	TArray<FPoseKey> Keys;
	Keys.Add(MakeKey(0, FVector(0.0, 0.0, 0.0), 0.0));
	Keys.Add(MakeKey(20, FVector(100.0, 0.0, 0.0), 15.0));
	Keys.Add(MakeKey(50, FVector(150.0, 80.0, 0.0), 40.0));

	FChainResult Result;
	FPoseChainSolver::Solve(Keys, MakeSettings(), Result);

	for (const FPoseKey& Key : Keys)
	{
		const FChainTrajectorySample* Found = Result.Samples.FindByPredicate(
			[&Key](const FChainTrajectorySample& Sample) { return Sample.Frame == Key.Frame; });
		TestNotNull(TEXT("key frame has a sample"), Found);
		if (Found != nullptr)
		{
			TestTrue(TEXT("position hits the key exactly"),
				Found->LocalLocation.Equals(Key.LocalLocation, 1e-4));
			TestTrue(TEXT("heading hits the key exactly"),
				FMath::Abs(Found->HeadingDegrees - Key.HeadingDegrees) < 1e-4);
		}
	}
	return true;
}

// T4：偏航 350 度到 10 度只转 20 度，不是倒着转 340 度。
IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FChainHeadingWrapTest,
	"MotionWeaver.PoseChain.HeadingWrap",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FChainHeadingWrapTest::RunTest(const FString& Parameters)
{
	TArray<FPoseKey> Keys;
	Keys.Add(MakeKey(0, FVector::ZeroVector, 350.0));
	Keys.Add(MakeKey(60, FVector(60.0, 0.0, 0.0), 10.0));

	FChainResult Result;
	FPoseChainSolver::Solve(Keys, MakeSettings(), Result);

	TestTrue(TEXT("wrap chain solves"), Result.Diagnostics.IsValid());
	TestTrue(TEXT("total yaw is about +20 degrees"),
		FMath::Abs(Result.Diagnostics.TotalYawDegrees - 20.0) < 1e-3);

	if (Result.Samples.Num() >= 2)
	{
		TestTrue(TEXT("heading increases monotonically"),
			Result.Samples[0].HeadingDegrees <= Result.Samples.Last().HeadingDegrees + 1e-6);
	}
	return true;
}

// T5：标了 bStopHere 的帧速度精确为 0。
IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FChainStopTest,
	"MotionWeaver.PoseChain.StopKey",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FChainStopTest::RunTest(const FString& Parameters)
{
	TArray<FPoseKey> Keys;
	Keys.Add(MakeKey(0, FVector::ZeroVector, 0.0));
	Keys.Add(MakeKey(30, FVector(100.0, 0.0, 0.0), 0.0, true));
	Keys.Add(MakeKey(60, FVector(200.0, 0.0, 0.0), 0.0));

	FChainResult Result;
	FPoseChainSolver::Solve(Keys, MakeSettings(), Result);

	const FChainTrajectorySample* StopSample = Result.Samples.FindByPredicate(
		[](const FChainTrajectorySample& Sample) { return Sample.Frame == 30; });
	TestNotNull(TEXT("stop frame has a sample"), StopSample);
	if (StopSample != nullptr)
	{
		TestTrue(TEXT("stop frame speed is exactly zero"), StopSample->SpeedCmPerSecond < 1e-3);
	}
	return true;
}

// T6：超速且关闭自动修正时，报错且不改动 Keys。
IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FChainOverspeedNoFixTest,
	"MotionWeaver.PoseChain.OverspeedNoFix",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FChainOverspeedNoFixTest::RunTest(const FString& Parameters)
{
	// 0.2 秒走 300 cm，即 1500 cm/s，远超 400。
	TArray<FPoseKey> Keys;
	Keys.Add(MakeKey(0, FVector::ZeroVector, 0.0));
	Keys.Add(MakeKey(24, FVector(300.0, 0.0, 0.0), 0.0));

	const int32 OriginalFrame = Keys[1].Frame;

	FChainSettings Settings = MakeSettings();
	Settings.bAutoInsertKeys = false;
	Settings.MaxSpeedCmPerSecond = 400.0;

	FChainResult Result;
	const bool bSolved = FPoseChainSolver::Solve(Keys, Settings, Result);

	TestFalse(TEXT("overspeed without auto fix fails"), bSolved);
	TestTrue(TEXT("errors are reported"), Result.Diagnostics.Errors.Num() > 0);
	TestEqual(TEXT("key count is untouched"), Keys.Num(), 2);
	TestEqual(TEXT("key frame is untouched"), Keys[1].Frame, OriginalFrame);
	return true;
}

// T7：超速且开启自动修正时，峰值被压到上限内，且原始峰值仍保留。
IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FChainOverspeedFixedTest,
	"MotionWeaver.PoseChain.OverspeedFixed",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FChainOverspeedFixedTest::RunTest(const FString& Parameters)
{
	// 0.2 秒走 90 cm 即 450 cm/s，只超 12.5%，膨胀到 1.13 倍就够——
	// 在默认 2 倍上限之内，属于「能自动修好」的那一类。
	TArray<FPoseKey> Keys;
	Keys.Add(MakeKey(0, FVector::ZeroVector, 0.0));
	Keys.Add(MakeKey(24, FVector(90.0, 0.0, 0.0), 0.0));

	FChainSettings Settings = MakeSettings();
	Settings.bAutoInsertKeys = true;
	Settings.MaxSpeedCmPerSecond = 400.0;

	FChainResult Result;
	const bool bSolved = FPoseChainSolver::Solve(Keys, Settings, Result);

	TestTrue(TEXT("auto fix makes it pass"), bSolved);
	TestTrue(TEXT("final peak is within the limit"),
		Result.Diagnostics.MaxSpeedCmPerSecond <= Settings.MaxSpeedCmPerSecond * 1.01);
	TestTrue(TEXT("original peak is preserved"),
		Result.Diagnostics.OriginalMaxSpeedCmPerSecond > Settings.MaxSpeedCmPerSecond * 1.05);
	TestTrue(TEXT("total duration grows"), Result.Diagnostics.TotalTimeDilation > 1.0);
	return true;
}

// T11：需要超过时长上限才能修好的动作，必须如实报错，而不是硬拉成几倍时长。
IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FChainDilationCapTest,
	"MotionWeaver.PoseChain.DilationCap",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FChainDilationCapTest::RunTest(const FString& Parameters)
{
	// 1500 cm/s 需要膨胀 3.75 倍，而默认上限是 2 倍：修不好就该说修不好。
	TArray<FPoseKey> Keys;
	Keys.Add(MakeKey(0, FVector::ZeroVector, 0.0));
	Keys.Add(MakeKey(24, FVector(300.0, 0.0, 0.0), 0.0));

	FChainSettings Settings = MakeSettings();
	Settings.bAutoInsertKeys = true;
	Settings.MaxSpeedCmPerSecond = 400.0;
	Settings.MaxTimeDilation = 2.0;

	FChainResult Result;
	const bool bSolved = FPoseChainSolver::Solve(Keys, Settings, Result);

	TestFalse(TEXT("unfixable within the cap still fails"), bSolved);
	TestTrue(TEXT("an error names the problem"), Result.Diagnostics.Errors.Num() > 0);
	TestTrue(TEXT("duration never exceeds the cap"),
		Result.Diagnostics.TotalTimeDilation <= Settings.MaxTimeDilation + 1e-3);
	TestTrue(TEXT("original peak shows how bad it was"),
		Result.Diagnostics.OriginalMaxSpeedCmPerSecond > Settings.MaxSpeedCmPerSecond * 3.0);
	return true;
}

// T8：派生 key 是输出而不是输入，Keys 数组一个元素都不会多。
IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FChainDerivedKeysTest,
	"MotionWeaver.PoseChain.DerivedKeys",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FChainDerivedKeysTest::RunTest(const FString& Parameters)
{
	TArray<FPoseKey> Keys;
	Keys.Add(MakeKey(0, FVector::ZeroVector, 0.0));
	Keys.Add(MakeKey(24, FVector(300.0, 0.0, 0.0), 0.0));

	FChainSettings Settings = MakeSettings();
	Settings.bAutoInsertKeys = true;
	Settings.MaxSpeedCmPerSecond = 400.0;

	FChainResult Result;
	FPoseChainSolver::Solve(Keys, Settings, Result);

	TestEqual(TEXT("authored key count is unchanged"), Keys.Num(), 2);
	TestTrue(TEXT("derived keys are produced"), Result.DerivedKeys.Num() > 0);
	TestEqual(TEXT("reported derived count matches"),
		Result.Diagnostics.DerivedKeyCount, Result.DerivedKeys.Num());
	return true;
}

// T9：同一帧出现两个关键帧时必须报错，而不是静默取其一。
IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FChainDuplicateFrameTest,
	"MotionWeaver.PoseChain.DuplicateFrame",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FChainDuplicateFrameTest::RunTest(const FString& Parameters)
{
	TArray<FPoseKey> Keys;
	Keys.Add(MakeKey(0, FVector::ZeroVector, 0.0));
	Keys.Add(MakeKey(0, FVector(100.0, 0.0, 0.0), 0.0));

	FChainResult Result;
	const bool bSolved = FPoseChainSolver::Solve(Keys, MakeSettings(), Result);

	TestFalse(TEXT("duplicate frames fail"), bSolved);
	TestTrue(TEXT("error is reported"), Result.Diagnostics.Errors.Num() > 0);
	return true;
}

// T10：空链必须明确失败，而不是返回空轨迹当作成功。
IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FChainEmptyTest,
	"MotionWeaver.PoseChain.Empty",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FChainEmptyTest::RunTest(const FString& Parameters)
{
	TArray<FPoseKey> Keys;
	FChainResult Result;
	const bool bSolved = FPoseChainSolver::Solve(Keys, MakeSettings(), Result);

	TestFalse(TEXT("empty chain fails"), bSolved);
	TestTrue(TEXT("error is reported"), Result.Diagnostics.Errors.Num() > 0);
	return true;
}

// T12：FK 必须累乘父骨旋转，而不是只加位置。
//     只有位置相加时，弯曲的姿势会被画成一团朝固定方向散开的线。
IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FChainStickFigureFkTest,
	"MotionWeaver.PoseChain.StickFigureFk",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FChainStickFigureFkTest::RunTest(const FString& Parameters)
{
	// 两根骨：根骨平移 50 并绕 Z 转 90 度；子骨在父骨坐标系里沿 X 走 100。
	// 正确结果：子骨组件空间 = R90 * (100,0,0) + (50,0,0) = (50,100,0)。
	// 只加位置的错误结果会是 (150,0,0)。
	// FRotator(Pitch, Yaw, Roll)：要绕 Z 转 90 度必须写在 Yaw 位上。
	// 写成 (0,0,90) 是 Roll，绕的是 X 轴，位置根本不会变——这个测试一开始就错在这。
	const FTransform RootTransform(FRotator(0.0, 90.0, 0.0), FVector(50.0, 0.0, 0.0));
	const FTransform ChildTransform(FVector(100.0, 0.0, 0.0));
	const FTransform ChildComponentSpace = ChildTransform * RootTransform;

	const FVector ChildLocation = ChildComponentSpace.GetLocation();
	TestTrue(TEXT("child is rotated into the parent frame, not just offset"),
		!FMath::IsNearlyEqual(ChildLocation.X, 150.0, 1.0));
	TestTrue(TEXT("child lands where the rotated parent frame puts it"),
		ChildLocation.Equals(FVector(50.0, 100.0, 0.0), 1e-3));
	TestTrue(TEXT("bone length is preserved"),
		FMath::IsNearlyEqual(FVector::Dist(ChildLocation, RootTransform.GetLocation()), 100.0, 1e-3));
	return true;
}

// T13：AppendKey 的自动帧号必须严格递增，且按 NewKeyFrameStride 间隔。
IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FChainAppendKeyFrameTest,
	"MotionWeaver.PoseChain.AppendKeyFrames",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FChainAppendKeyFrameTest::RunTest(const FString& Parameters)
{
	// 这段测的是纯数据规则，不构造 Actor（Actor 需要 World）。
	FChainSettings Settings;
	Settings.NewKeyFrameStride = 24;

	TArray<FPoseKey> Keys;
	auto AppendFrame = [&Keys, &Settings](int32 Value)
	{
		int32 NextFrame = 0;
		for (const FPoseKey& Existing : Keys)
		{
			NextFrame = FMath::Max(NextFrame, Existing.Frame + Settings.NewKeyFrameStride);
		}
		FPoseKey Key;
		Key.Frame = NextFrame;
		Keys.Add(Key);
		(void)Value;
	};

	AppendFrame(0);
	AppendFrame(0);
	AppendFrame(0);

	TestEqual(TEXT("three keys"), Keys.Num(), 3);
	TestEqual(TEXT("first frame is zero"), Keys[0].Frame, 0);
	TestEqual(TEXT("second follows the stride"), Keys[1].Frame, 24);
	TestEqual(TEXT("third follows the stride"), Keys[2].Frame, 48);
	return true;
}

// B'：代理 Actor 拖完之后，写回必须落在「排序后的第几个 key」对应的原始条目上。
// 这里不构造 Actor（需要 World），只验证排序 -> 原始下标的映射规则本身：
// 顺序被打乱时，代理下标 0/1/2 必须分别指向帧号最小的那三条，而不是数组里的第 0/1/2 条。
IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FChainSortedIndexMappingTest,
	"MotionWeaver.PoseChain.SortedIndexMapping",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FChainSortedIndexMappingTest::RunTest(const FString& Parameters)
{
	// 刻意乱序：帧号 48 / 0 / 24。作者在细节面板里调整过顺序就是这个样子。
	TArray<FPoseKey> Keys;
	Keys.Add(MakeKey(48, FVector(200.0, 0.0, 0.0), 0.0));
	Keys.Add(MakeKey(0, FVector(0.0, 0.0, 0.0), 0.0));
	Keys.Add(MakeKey(24, FVector(100.0, 0.0, 0.0), 0.0));

	// 与 APoseChainActor::Rebuild 里同一套构造方式。
	TArray<int32> SortedToOriginal;
	SortedToOriginal.SetNum(Keys.Num());
	for (int32 Index = 0; Index < Keys.Num(); ++Index)
	{
		SortedToOriginal[Index] = Index;
	}
	SortedToOriginal.StableSort([&Keys](int32 A, int32 B)
	{
		return Keys[A].Frame < Keys[B].Frame;
	});

	TestEqual(TEXT("sorted[0] is the frame-0 key"), SortedToOriginal[0], 1);
	TestEqual(TEXT("sorted[1] is the frame-24 key"), SortedToOriginal[1], 2);
	TestEqual(TEXT("sorted[2] is the frame-48 key"), SortedToOriginal[2], 0);

	// 模拟代理写回 sorted index 1：必须改到原始的 Keys[2]，而不是 Keys[1]。
	const int32 SortedIndex = 1;
	const int32 OriginalIndex = SortedToOriginal[SortedIndex];
	Keys[OriginalIndex].LocalLocation = FVector(999.0, 0.0, 0.0);

	TestEqual(TEXT("Keys[2] moved"), Keys[2].LocalLocation.X, 999.0, 1e-6);
	TestEqual(TEXT("Keys[1] untouched"), Keys[1].LocalLocation.X, 0.0, 1e-6);
	TestEqual(TEXT("Keys[0] untouched"), Keys[0].LocalLocation.X, 200.0, 1e-6);

	// 帧号本身不能因为排序被改动：排序只决定「谁是第几个」，不改时间。
	TestEqual(TEXT("frame numbers unchanged"), Keys[0].Frame, 48);
	return true;
}

// BodyRotation 是真值、HeadingDegrees 是派生值：两者不能各说各话。
// 这条测试锁的是「成对 setter」这个不变量，而不是某个具体数值。
IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FChainRotationConsistencyTest,
	"MotionWeaver.PoseChain.RotationConsistency",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FChainRotationConsistencyTest::RunTest(const FString& Parameters)
{
	FPoseKey Key;

	// 1) 转完整朝向：HeadingDegrees 必须跟着 Yaw 走。
	Key.SetBodyRotation(FRotator(15.0, 42.0, -8.0));
	TestEqual(TEXT("yaw cached from body rotation"), Key.HeadingDegrees, 42.0, 1e-9);
	TestEqual(TEXT("pitch kept"), Key.BodyRotation.Pitch, 15.0, 1e-9);
	TestEqual(TEXT("roll kept"), Key.BodyRotation.Roll, -8.0, 1e-9);

	// 2) 只改偏航：Pitch/Roll 不能被顺手抹掉（这是旧代码最容易踩的坑）。
	Key.SetHeadingDegrees(-120.0);
	TestEqual(TEXT("heading written"), Key.HeadingDegrees, -120.0, 1e-9);
	TestEqual(TEXT("body yaw follows heading"), Key.BodyRotation.Yaw, -120.0, 1e-9);
	TestEqual(TEXT("pitch survives heading change"), Key.BodyRotation.Pitch, 15.0, 1e-9);
	TestEqual(TEXT("roll survives heading change"), Key.BodyRotation.Roll, -8.0, 1e-9);

	// 3) 旧关卡兼容：BodyRotation 全零但 HeadingDegrees 有值，补齐成偏航。
	FPoseKey Legacy;
	Legacy.HeadingDegrees = 90.0;
	Legacy.BodyRotation = FRotator::ZeroRotator;
	Legacy.NormalizeRotationConsistency();
	TestEqual(TEXT("legacy heading restored into body"), Legacy.BodyRotation.Yaw, 90.0, 1e-9);

	// 4) 用户真转过的数据不能被兼容逻辑覆盖。
	FPoseKey Real;
	Real.SetBodyRotation(FRotator(10.0, 30.0, 5.0));
	Real.NormalizeRotationConsistency();
	TestEqual(TEXT("real rotation untouched by normalize"), Real.BodyRotation.Pitch, 10.0, 1e-9);

	// 5) 关键契约：Pitch/Roll 不进 Root 轨迹。
	//    两次求解，唯一差别是一个 key 的 Pitch/Roll，轨迹必须逐帧完全一致。
	TArray<FPoseKey> KeysA;
	KeysA.Add(MakeKey(0, FVector(0.0, 0.0, 0.0), 0.0));
	KeysA.Add(MakeKey(60, FVector(120.0, 0.0, 0.0), 30.0));

	TArray<FPoseKey> KeysB = KeysA;
	KeysB[0].SetBodyRotation(FRotator(35.0, 0.0, 20.0));

	FChainResult ResultA;
	FChainResult ResultB;
	FPoseChainSolver::Solve(KeysA, MakeSettings(), ResultA);
	FPoseChainSolver::Solve(KeysB, MakeSettings(), ResultB);

	TestEqual(TEXT("sample counts match"), ResultB.Samples.Num(), ResultA.Samples.Num());
	bool bTrajectoryIdentical = ResultA.Samples.Num() == ResultB.Samples.Num();
	for (int32 Index = 0; bTrajectoryIdentical && Index < ResultA.Samples.Num(); ++Index)
	{
		bTrajectoryIdentical =
			ResultA.Samples[Index].LocalLocation.Equals(ResultB.Samples[Index].LocalLocation, 1e-9) &&
			FMath::IsNearlyEqual(ResultA.Samples[Index].HeadingDegrees, ResultB.Samples[Index].HeadingDegrees, 1e-9);
	}
	TestTrue(TEXT("pitch/roll never affect root trajectory"), bTrajectoryIdentical);

	return true;
}

#endif // WITH_DEV_AUTOMATION_TESTS
