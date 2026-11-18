// Copyright ZhaoZining. All Rights Reserved.

#include "RefPose/RefPoseModel.h"

#include "Misc/FileHelper.h"
#include "NNEModelData.h"

URefPoseModelAsset* URefPoseModelAsset::CreateFromOnnxFile(const FString& OnnxPath, FString& OutError)
{
	TArray<uint8> Bytes;
	if (!FFileHelper::LoadFileToArray(Bytes, *OnnxPath) || Bytes.IsEmpty())
	{
		OutError = FString::Printf(TEXT("无法读取 ONNX：%s"), *OnnxPath);
		return nullptr;
	}

	URefPoseModelAsset* Asset = NewObject<URefPoseModelAsset>();
	Asset->ModelData = NewObject<UNNEModelData>(Asset);
	Asset->ModelData->Init(TEXT("onnx"), MakeArrayView(Bytes));
	Asset->ModelData->SetTargetRuntimes({ TEXT("NNERuntimeORTCpu") });
	return Asset;
}
