// Copyright ZhaoZining. All Rights Reserved.

#include "CoreMinimal.h"
#include "HAL/PlatformMisc.h"
#include "Misc/AutomationTest.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "RefPose/RefPoseModel.h"
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
	// 数量下限从包本身推导，并额外要求至少一组带参考帧；否则一份全零 mask 的夹具集
	// 会让「mask 精确」这条断言在 0 个元素上通过。
	const int32 ExpectedFixtures = 3 * 2 * 3 * 4;
	const int32 ExpectedValues = ExpectedFixtures * FrameCount * PoseFeatures;
	TestEqual(TEXT("比较全部声明夹具"), Fixtures->Num(), ExpectedFixtures);
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
			&& Fixtures->Num() == ExpectedFixtures && ComparedValues == ExpectedValues
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

#endif
