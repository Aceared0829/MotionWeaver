// Copyright ZhaoZining. All Rights Reserved.

#include "RefPose/RefPoseSession.h"

#include "NNE.h"
#include "NNEModelData.h"
#include "NNETypes.h"
#include "RefPose/RefPoseModel.h"

TSharedPtr<FRefPoseSession> FRefPoseSession::Create(
	const URefPoseModelAsset& Asset, FString& OutError)
{
	check(IsInGameThread());
	if (!Asset.ModelData || Asset.BoneCount <= 0 || Asset.HistoryFrames <= 0 || Asset.FutureFrames <= 0)
	{
		OutError = TEXT("RefPose 模型或窗口契约无效。");
		return nullptr;
	}

	TWeakInterfacePtr<INNERuntimeCPU> Runtime =
		UE::NNE::GetRuntime<INNERuntimeCPU>(TEXT("NNERuntimeORTCpu"));
	if (!Runtime.IsValid() || Runtime->CanCreateModelCPU(Asset.ModelData) != UE::NNE::EResultStatus::Ok)
	{
		OutError = TEXT("NNERuntimeORTCpu 不可用或拒绝该 ONNX。");
		return nullptr;
	}

	TSharedPtr<FRefPoseSession> Session = MakeShared<FRefPoseSession>();
	Session->ModelData.Reset(Asset.ModelData);
	Session->Model = Runtime->CreateModelCPU(Asset.ModelData);
	Session->Instance = Session->Model ? Session->Model->CreateModelInstanceCPU() : nullptr;
	if (!Session->Instance)
	{
		OutError = TEXT("无法创建 RefPose NNE 实例。");
		return nullptr;
	}

	Session->BoneCount = Asset.BoneCount;
	Session->HistoryFrames = Asset.HistoryFrames;
	Session->FutureFrames = Asset.FutureFrames;
	const uint32 PoseFeatures = static_cast<uint32>(Asset.BoneCount * 9);
	const TArray<UE::NNE::FTensorShape> Shapes = {
		UE::NNE::FTensorShape::Make({ 1u, static_cast<uint32>(Asset.HistoryFrames), PoseFeatures + 7u }),
		UE::NNE::FTensorShape::Make({ 1u, static_cast<uint32>(Asset.FutureFrames), 7u }),
		UE::NNE::FTensorShape::Make({ 1u, static_cast<uint32>(Asset.FutureFrames), PoseFeatures }),
		UE::NNE::FTensorShape::Make({ 1u, static_cast<uint32>(Asset.FutureFrames), 1u }),
	};
	if (Session->Instance->SetInputTensorShapes(Shapes) != UE::NNE::EResultStatus::Ok)
	{
		OutError = TEXT("ONNX 拒绝 RefPose 四输入形状。");
		return nullptr;
	}

	const TConstArrayView<UE::NNE::FTensorDesc> Inputs = Session->Instance->GetInputTensorDescs();
	const TConstArrayView<UE::NNE::FTensorDesc> Outputs = Session->Instance->GetOutputTensorDescs();
	const TCHAR* ExpectedNames[] = { TEXT("history"), TEXT("root_plan"), TEXT("reference"), TEXT("mask") };
	if (Inputs.Num() != 4 || Outputs.Num() != 1 || Outputs[0].GetName() != TEXT("joints")
		|| Outputs[0].GetDataType() != ENNETensorDataType::Float)
	{
		OutError = TEXT("ONNX 输入/输出数量或 joints 描述不符合契约。");
		return nullptr;
	}
	for (int32 Index = 0; Index < Inputs.Num(); ++Index)
	{
		if (Inputs[Index].GetName() != ExpectedNames[Index]
			|| Inputs[Index].GetDataType() != ENNETensorDataType::Float
			|| Session->Instance->GetInputTensorShapes()[Index] != Shapes[Index])
		{
			OutError = FString::Printf(TEXT("输入 %s 的名称、dtype 或形状不符。"), ExpectedNames[Index]);
			return nullptr;
		}
	}
	const TConstArrayView<UE::NNE::FTensorShape> OutputShapes = Session->Instance->GetOutputTensorShapes();
	if (OutputShapes.Num() != 1
		|| OutputShapes[0].Volume() != static_cast<uint64>(Asset.FutureFrames * PoseFeatures))
	{
		OutError = TEXT("joints 输出形状不符合契约。");
		return nullptr;
	}
	return Session;
}

bool FRefPoseSession::Run(
	const FRefPoseInputTensors& Inputs,
	TArray<float>& OutJoints,
	double& OutMilliseconds,
	FString& OutError) const
{
	const int32 PoseFeatures = BoneCount * 9;
	const int32 Expected[] = {
		HistoryFrames * (PoseFeatures + 7), FutureFrames * 7,
		FutureFrames * PoseFeatures, FutureFrames,
	};
	const int32 Actual[] = {
		Inputs.History.Num(), Inputs.RootPlan.Num(), Inputs.Reference.Num(), Inputs.Mask.Num(),
	};
	for (int32 Index = 0; Index < 4; ++Index)
	{
		if (Actual[Index] != Expected[Index])
		{
			OutError = FString::Printf(TEXT("输入 %d 元素数错误：%d，期望 %d。"), Index, Actual[Index], Expected[Index]);
			return false;
		}
	}
	OutJoints.SetNumUninitialized(FutureFrames * PoseFeatures);
	const UE::NNE::FTensorBindingCPU InputBindings[] = {
		{ const_cast<float*>(Inputs.History.GetData()), static_cast<uint64>(Inputs.History.Num() * sizeof(float)) },
		{ const_cast<float*>(Inputs.RootPlan.GetData()), static_cast<uint64>(Inputs.RootPlan.Num() * sizeof(float)) },
		{ const_cast<float*>(Inputs.Reference.GetData()), static_cast<uint64>(Inputs.Reference.Num() * sizeof(float)) },
		{ const_cast<float*>(Inputs.Mask.GetData()), static_cast<uint64>(Inputs.Mask.Num() * sizeof(float)) },
	};
	const UE::NNE::FTensorBindingCPU OutputBinding = {
		OutJoints.GetData(), static_cast<uint64>(OutJoints.Num() * sizeof(float)),
	};
	const double Start = FPlatformTime::Seconds();
	const UE::NNE::EResultStatus Status = Instance->RunSync(InputBindings, MakeArrayView(&OutputBinding, 1));
	OutMilliseconds = (FPlatformTime::Seconds() - Start) * 1000.0;
	if (Status != UE::NNE::EResultStatus::Ok)
	{
		OutError = TEXT("RefPose NNE RunSync 失败。");
		return false;
	}
	for (const float Value : OutJoints)
	{
		if (!FMath::IsFinite(Value))
		{
			OutError = TEXT("RefPose NNE 输出包含 NaN/Inf。");
			return false;
		}
	}
	return true;
}
