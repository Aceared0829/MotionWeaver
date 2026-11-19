// Copyright ZhaoZining. All Rights Reserved.

#include "CoreMinimal.h"
#include "HAL/PlatformMisc.h"
#include "Misc/AutomationTest.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "RefPose/RefPoseModel.h"
#include "RefPose/RefPosePoseContract.h"
#include "RefPose/RefPoseSession.h"
#include "Policies/CondensedJsonPrintPolicy.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "UObject/StrongObjectPtr.h"

#include <openssl/sha.h>

#if WITH_DEV_AUTOMATION_TESTS

namespace
{
	constexpr int32 BoneCount = 78;
	constexpr int32 FrameCount = 24;
	constexpr int32 PoseFeatures = BoneCount * 9;
	constexpr double MaxAbsTolerance = 1e-4;

	bool LoadFloatArray(const FString& Path, int32 ExpectedCount, TArray<float>& Values, FString& OutError)
	{
		TArray<uint8> Bytes;
		if (!FFileHelper::LoadFileToArray(Bytes, *Path)
			|| Bytes.Num() != static_cast<int64>(ExpectedCount * sizeof(float)))
		{
			OutError = FString::Printf(TEXT("读取失败或元素数不符：%s"), *Path);
			return false;
		}
		Values.SetNumUninitialized(ExpectedCount);
		FMemory::Memcpy(Values.GetData(), Bytes.GetData(), Bytes.Num());
		return true;
	}

	bool LoadPackageBytes(const FString& Path, const TArray<TSharedPtr<FJsonValue>>& Artifacts, FString& OutError)
	{
		TArray<uint8> Bytes;
		if (!FFileHelper::LoadFileToArray(Bytes, *Path) || Bytes.IsEmpty())
		{
			OutError = FString::Printf(TEXT("无法读取模型：%s"), *Path);
			return false;
		}
		uint8 Digest[SHA256_DIGEST_LENGTH];
		SHA256(Bytes.GetData(), Bytes.Num(), Digest);
		const FString Actual = BytesToHex(Digest, SHA256_DIGEST_LENGTH).ToLower();
		for (const TSharedPtr<FJsonValue>& ArtifactValue : Artifacts)
		{
			const TSharedPtr<FJsonObject> Artifact = ArtifactValue->AsObject();
			if (Artifact.IsValid() && Artifact->GetStringField(TEXT("name")) == TEXT("model.onnx")
				&& Artifact->GetStringField(TEXT("sha256")) == Actual)
			{
				return true;
			}
		}
		OutError = TEXT("model.onnx SHA-256 与 package.json 不符。");
		return false;
	}

	TSharedPtr<FJsonObject> LoadObject(const FString& Path)
	{
		FString Text;
		TSharedPtr<FJsonObject> Object;
		if (FFileHelper::LoadFileToString(Text, *Path))
		{
			FJsonSerializer::Deserialize(
				TJsonReaderFactory<TCHAR>::Create(Text), Object);
		}
		return Object;
	}

	// UE 的 GetStringField / GetObjectField 在字段缺失时是 check 崩溃而不是返回空值。
	// 这是校验外部导出包的测试，畸形 package.json 必须报失败，不能把编辑器打崩。
	FString StringField(const TSharedPtr<FJsonObject>& Object, const TCHAR* Field, FString& OutError)
	{
		FString Value;
		if (!Object.IsValid() || !Object->TryGetStringField(Field, Value))
		{
			OutError = FString::Printf(TEXT("package.json 缺少字符串字段 %s。"), Field);
		}
		return Value;
	}

	TSharedPtr<FJsonObject> ObjectField(const TSharedPtr<FJsonObject>& Object, const TCHAR* Field, FString& OutError)
	{
		const TSharedPtr<FJsonObject>* Value = nullptr;
		if (!Object.IsValid() || !Object->TryGetObjectField(Field, Value) || Value == nullptr)
		{
			OutError = FString::Printf(TEXT("package.json 缺少对象字段 %s。"), Field);
			return nullptr;
		}
		return *Value;
	}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FRefPoseNneNumericTest, "MotionWeaver.RefPose.NNE.Numeric",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRefPoseNneNumericTest::RunTest(const FString& Parameters)
{
	const FString PackageDir = FPlatformMisc::GetEnvironmentVariable(TEXT("MW_REFPOSE_UE_PACKAGE"));
	if (!TestTrue(TEXT("MW_REFPOSE_UE_PACKAGE 指向阶段二导出包"), !PackageDir.IsEmpty()))
	{
		return true;
	}
	const TSharedPtr<FJsonObject> Package = LoadObject(PackageDir / TEXT("package.json"));
	if (!TestTrue(TEXT("读取 package.json"), Package.IsValid()))
	{
		return true;
	}
	FString Error;
	const FString Format = StringField(Package, TEXT("format"), Error);
	if (!TestTrue(TEXT("package.json 含 format"), Error.IsEmpty()))
	{
		AddError(Error);
		return true;
	}
	TestEqual(TEXT("包格式"), Format, TEXT("refpose_ue_nne_stage2_v2"));
	const TSharedPtr<FJsonObject> Onnx = ObjectField(Package, TEXT("onnx"), Error);
	if (!TestNotNull(TEXT("package.json 含 onnx 段"), Onnx.Get()))
	{
		AddError(Error);
		return true;
	}
	TestEqual(TEXT("ONNX opset"), static_cast<int32>(Onnx->GetNumberField(TEXT("opset"))), 17);

	const TArray<TSharedPtr<FJsonValue>>* Artifacts = nullptr;
	if (!TestTrue(TEXT("产物哈希清单存在"),
		Package->TryGetArrayField(TEXT("artifacts"), Artifacts) && Artifacts != nullptr))
	{
		return true;
	}
	if (!TestTrue(TEXT("model.onnx 哈希匹配"), LoadPackageBytes(
		PackageDir / TEXT("model.onnx"), *Artifacts, Error)))
	{
		AddError(Error);
		return true;
	}
	TStrongObjectPtr<URefPoseModelAsset> Asset(
		URefPoseModelAsset::CreateFromOnnxFile(PackageDir / TEXT("model.onnx"), Error));
	if (!TestNotNull(TEXT("创建模型资产"), Asset.Get()) || !Asset.IsValid())
	{
		AddError(Error);
		return true;
	}
	const TSharedPtr<FJsonObject> Selected = ObjectField(Package, TEXT("selected_checkpoint"), Error);
	Asset->CheckpointSha256 = StringField(Selected, TEXT("sha256"), Error);
	Asset->OnnxSha256 = StringField(Onnx, TEXT("onnx_sha256"), Error);
	if (!TestTrue(TEXT("package.json 含检查点与 ONNX 哈希"), Error.IsEmpty()))
	{
		AddError(Error);
		return true;
	}
	TSharedPtr<FRefPoseSession> Session = FRefPoseSession::Create(*Asset, Error);
	if (!TestTrue(TEXT("创建四输入 NNE CPU 会话"), Session.IsValid()))
	{
		AddError(Error);
		return true;
	}

	const TArray<TSharedPtr<FJsonValue>>* Fixtures = nullptr;
	if (!TestTrue(TEXT("夹具列表存在"), Package->TryGetArrayField(TEXT("fixtures"), Fixtures) && Fixtures != nullptr))
	{
		return true;
	}
	double MaxAbs = 0.0;
	double MaxMaskAbs = 0.0;
	TArray<double> InferenceMs;
	InferenceMs.Reserve(Fixtures->Num());
	int32 ComparedValues = 0;
	int32 MaskValues = 0;
	for (const TSharedPtr<FJsonValue>& FixtureValue : *Fixtures)
	{
		const TSharedPtr<FJsonObject> Fixture = FixtureValue->AsObject();
		const FString Directory = PackageDir / TEXT("fixtures") / Fixture->GetStringField(TEXT("directory"));
		FRefPoseInputTensors Inputs;
		TArray<float> History, RootPlan, Reference, Mask, Expected;
		bool bLoaded = LoadFloatArray(Directory / TEXT("history.f32"), FrameCount * (PoseFeatures + 7), History, Error)
			&& LoadFloatArray(Directory / TEXT("root_plan.f32"), FrameCount * 7, RootPlan, Error)
			&& LoadFloatArray(Directory / TEXT("reference.f32"), FrameCount * PoseFeatures, Reference, Error)
			&& LoadFloatArray(Directory / TEXT("mask.f32"), FrameCount, Mask, Error)
			&& LoadFloatArray(Directory / TEXT("expected.f32"), FrameCount * PoseFeatures, Expected, Error);
		if (!TestTrue(TEXT("读取真实窗口夹具"), bLoaded))
		{
			AddError(Error);
			break;
		}
		Inputs = { History, RootPlan, Reference, Mask };
		TArray<float> Actual;
		double Milliseconds = 0.0;
		if (!TestTrue(TEXT("NNE 执行成功"), Session->Run(Inputs, Actual, Milliseconds, Error)))
		{
			AddError(Error);
			break;
		}
		InferenceMs.Add(Milliseconds);
		for (int32 Index = 0; Index < Expected.Num(); ++Index)
		{
			MaxAbs = FMath::Max(MaxAbs, FMath::Abs(static_cast<double>(Actual[Index]) - Expected[Index]));
		}
		ComparedValues += Expected.Num();
		for (int32 Frame = 0; Frame < FrameCount; ++Frame)
		{
			if (Mask[Frame] <= 0.5f)
			{
				continue;
			}
			for (int32 Feature = 0; Feature < PoseFeatures; ++Feature)
			{
				const int32 Index = Frame * PoseFeatures + Feature;
				MaxMaskAbs = FMath::Max(MaxMaskAbs,
					FMath::Abs(static_cast<double>(Actual[Index]) - Reference[Index]));
				++MaskValues;
			}
		}
	}
	InferenceMs.Sort();
	const double P50 = InferenceMs.IsEmpty() ? 0.0 : InferenceMs[InferenceMs.Num() / 2];
	const double P95 = InferenceMs.IsEmpty() ? 0.0 : InferenceMs[FMath::Min(
		InferenceMs.Num() - 1, FMath::CeilToInt(InferenceMs.Num() * 0.95) - 1)];
	// 数量从包自身推导，并额外要求至少一组带参考帧；否则一份全零 mask 的夹具集
	// 会让「mask 精确」这条断言在 0 个元素上通过。
	const int32 DeclaredFixtures = static_cast<int32>(Package->GetNumberField(TEXT("fixture_count")));
	const int32 ExpectedValues = DeclaredFixtures * FrameCount * PoseFeatures;
	TestEqual(TEXT("遍历的夹具数与包声明一致"), Fixtures->Num(), DeclaredFixtures);
	TestTrue(TEXT("包声明了实际夹具"), DeclaredFixtures > 0);
	TestEqual(TEXT("比较全部输出元素"), ComparedValues, ExpectedValues);
	TestTrue(TEXT("至少校验过一个硬参考帧"), MaskValues > 0);
	TestTrue(TEXT("UE NNE 对 Python ORT 最大绝对误差 <= 1e-4"), MaxAbs <= MaxAbsTolerance);
	TestTrue(TEXT("mask 硬覆盖逐元素精确"), MaxMaskAbs == 0.0 && MaskValues > 0);
	AddInfo(FString::Printf(TEXT("RefPose NNE: fixtures=%d values=%d mask_values=%d max_abs=%.9g mask_abs=%.9g p50=%.3f ms p95=%.3f ms"),
		Fixtures->Num(), ComparedValues, MaskValues, MaxAbs, MaxMaskAbs, P50, P95));

	const FString ReportPath = FPlatformMisc::GetEnvironmentVariable(TEXT("MW_REFPOSE_UE_REPORT"));
	if (!ReportPath.IsEmpty())
	{
		TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>();
		Report->SetStringField(TEXT("format"), TEXT("refpose_ue_nne_stage2_report_v1"));
		Report->SetStringField(TEXT("checkpoint_sha256"), Asset->CheckpointSha256);
		Report->SetStringField(TEXT("onnx_sha256"), Asset->OnnxSha256);
		Report->SetStringField(TEXT("runtime"), TEXT("NNERuntimeORTCpu"));
		Report->SetNumberField(TEXT("fixture_count"), Fixtures->Num());
		Report->SetNumberField(TEXT("compared_values"), ComparedValues);
		Report->SetNumberField(TEXT("mask_values"), MaskValues);
		Report->SetNumberField(TEXT("max_abs"), MaxAbs);
		Report->SetNumberField(TEXT("max_mask_abs"), MaxMaskAbs);
		Report->SetNumberField(TEXT("p50_ms"), P50);
		Report->SetNumberField(TEXT("p95_ms"), P95);
		Report->SetBoolField(TEXT("passed"), MaxAbs <= MaxAbsTolerance && MaxMaskAbs == 0.0
			&& Fixtures->Num() == DeclaredFixtures && ComparedValues == ExpectedValues
			&& MaskValues > 0);
		FString Text;
		const TSharedRef<TJsonWriter<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>> Writer =
			TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&Text);
		FJsonSerializer::Serialize(Report, Writer);
		TestTrue(TEXT("写出 UE 数值报告"), FFileHelper::SaveStringToFile(Text, *ReportPath));
	}
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FRefPoseNneRejectsBadInputTest, "MotionWeaver.RefPose.NNE.RejectsBadInput",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRefPoseNneRejectsBadInputTest::RunTest(const FString& Parameters)
{
	const FString PackageDir = FPlatformMisc::GetEnvironmentVariable(TEXT("MW_REFPOSE_UE_PACKAGE"));
	if (!TestTrue(TEXT("MW_REFPOSE_UE_PACKAGE 指向阶段二导出包"), !PackageDir.IsEmpty()))
	{
		return true;
	}
	FString Error;
	TStrongObjectPtr<URefPoseModelAsset> Asset(
		URefPoseModelAsset::CreateFromOnnxFile(PackageDir / TEXT("model.onnx"), Error));
	if (!TestNotNull(TEXT("创建模型资产"), Asset.Get()) || !Asset.IsValid())
	{
		return true;
	}
	TSharedPtr<FRefPoseSession> Session = FRefPoseSession::Create(*Asset, Error);
	if (!TestTrue(TEXT("创建 NNE 会话"), Session.IsValid()))
	{
		AddError(Error);
		return true;
	}
	const FRefPoseInputTensors Inputs;
	TArray<float> Output;
	double Milliseconds = 0.0;
	TestFalse(TEXT("空输入被拒绝"), Session->Run(Inputs, Output, Milliseconds, Error));
	TestFalse(TEXT("返回可诊断错误"), Error.IsEmpty());
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FRefPoseSolverRealOutputTest,
	"MotionWeaver.RefPose.Contract.SolverRebuildsRealOutput",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRefPoseSolverRealOutputTest::RunTest(const FString& Parameters)
{
	// 用真实窗口夹具的模型输出验证解算器：位置解码后逐骨求方向，
	// 再用局部变换复算，方向必须逐骨回到模型输出。这条测试绕过了输入装配，
	// 专门抓「解码布局」和「局部旋转未累计父级」两类问题。
	const FString PackageDir = FPlatformMisc::GetEnvironmentVariable(TEXT("MW_REFPOSE_UE_PACKAGE"));
	if (PackageDir.IsEmpty())
	{
		AddInfo(TEXT("未设置 MW_REFPOSE_UE_PACKAGE，跳过真实输出解算检查。"));
		return true;
	}

	FString Error;
	TStrongObjectPtr<URefPoseModelAsset> Asset(NewObject<URefPoseModelAsset>());
	if (!TestTrue(TEXT("读取模型契约"), Asset->LoadContractFromPackage(PackageDir, Error)))
	{
		AddError(Error);
		return true;
	}

	TArray<float> Raw;
	const FString FixtureDir = PackageDir / TEXT("fixtures/sample_000");
	if (!TestTrue(TEXT("读取 expected.f32"), LoadFloatArray(
		FixtureDir / TEXT("expected.f32"), FrameCount * PoseFeatures, Raw, Error)))
	{
		AddError(Error);
		return true;
	}

	TArray<FRefPosePoseContract::FDecodedFrame> Decoded;
	if (!TestTrue(TEXT("解码真实输出"), FRefPosePoseContract::DecodeWindow(
		Raw, BoneCount, FrameCount, Asset->PoseMean, Asset->PoseStd, Decoded, Error)))
	{
		AddError(Error);
		return true;
	}

	TArray<int32> Parents;
	TArray<FTransform> Reference;
	Parents.Reserve(Asset->Bones.Num());
	Reference.Reserve(Asset->Bones.Num());
	for (const FRefPoseBoneContract& Bone : Asset->Bones)
	{
		Parents.Add(Bone.Parent);
		Reference.Add(FTransform(
			FQuat::Identity, FVector(Bone.LocalPositionMeters * 100.0)));
	}

	TArray<TArray<FTransform>> Local;
	FRefPoseSkeletonSolver::FMetrics Metrics;
	if (!TestTrue(TEXT("解算真实输出"), FRefPoseSkeletonSolver::Solve(
		Parents, Reference, Decoded, Local, Metrics, Error)))
	{
		AddError(Error);
		return true;
	}
	TestEqual(TEXT("退化骨数"), Metrics.DegenerateBones, 0);
	TestTrue(TEXT("骨长偏差为零"),
		FMath::IsNearlyZero(Metrics.MaxBoneLengthDeviationCm, 1e-9));

	TArray<TArray<FVector>> Rebuilt;
	if (!TestTrue(TEXT("复算真实输出"), FRefPoseSkeletonSolver::RebuildSkeletonPositions(
		Parents, Local, Rebuilt, Error)))
	{
		AddError(Error);
		return true;
	}

	int32 ComparedDirections = 0;
	for (int32 Frame = 0; Frame < Rebuilt.Num(); ++Frame)
	{
		for (int32 Bone = 0; Bone < Parents.Num(); ++Bone)
		{
			const int32 Parent = Parents[Bone];
			if (Parent < 0)
			{
				continue;
			}
			const FVector3d Expected =
				Decoded[Frame].PositionsMeters[Bone]
				- Decoded[Frame].PositionsMeters[Parent];
			if (Expected.IsNearlyZero(1e-6))
			{
				continue;
			}
			const FVector Actual =
				Rebuilt[Frame][Bone] - Rebuilt[Frame][Parent];
			const FVector3d ActualMotion =
				FRefPosePoseContract::ToMotionPosition(Actual);
			if (!TestTrue(
				FString::Printf(TEXT("帧 %d 骨 %d 方向"), Frame, Bone),
				ActualMotion.GetSafeNormal().Equals(
					Expected.GetSafeNormal(), 1e-5)))
			{
				return true;
			}
			++ComparedDirections;
		}
	}
	TestTrue(TEXT("比较了足够多的骨方向"), ComparedDirections > 1000);
	return true;
}

#endif
