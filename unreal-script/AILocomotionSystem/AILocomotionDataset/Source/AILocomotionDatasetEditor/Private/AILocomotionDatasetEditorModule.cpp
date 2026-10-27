// Copyright ZhaoZining. All Rights Reserved.

/** 在编辑器主菜单导出选中的动画；保持源资产不变，仅发布完整的数据批次。 */
#include "AILocomotionDatasetSettings.h"
#include "AnimPose.h"
#include "Animation/AnimSequence.h"
#include "Animation/AnimData/IAnimationDataModel.h"
#include "Animation/Skeleton.h"
#include "AssetRegistry/AssetData.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "ContentBrowserModule.h"
#include "Containers/Ticker.h"
#include "IContentBrowserSingleton.h"
#include "Interfaces/IPluginManager.h"
#include "Dom/JsonObject.h"
#include "HAL/FileManager.h"
#include "Misc/CommandLine.h"
#include "Misc/DateTime.h"
#include "Misc/EngineVersion.h"
#include "Misc/FileHelper.h"
#include "Misc/Guid.h"
#include "Misc/MessageDialog.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "Misc/ScopedSlowTask.h"
#include "Misc/SecureHash.h"
#include "Modules/ModuleManager.h"
#include "Serialization/JsonSerializer.h"
#include "ToolMenus.h"

#define LOCTEXT_NAMESPACE "AILocomotionDataset"

namespace
{
	TArray<TSharedPtr<FJsonValue>> Numbers(std::initializer_list<double> Values)
	{
		TArray<TSharedPtr<FJsonValue>> Result;
		for (double Value : Values)
		{
			Result.Add(MakeShared<FJsonValueNumber>(Value));
		}
		return Result;
	}

	bool WriteJson(const FString& Path, const TSharedRef<FJsonObject>& Object)
	{
		FString Text;
		return FJsonSerializer::Serialize(Object, TJsonWriterFactory<>::Create(&Text)) && FFileHelper::SaveStringToFile(Text, *Path, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
	}

	/**
	 * 内容身份：对已写出的文件计算 SHA-1，返回十六进制字符串。
	 *
	 * UE 5.8 的 FPlatformMisc::GetSHA256Signature 只有通用基类的占位实现
	 * （GenericPlatformMisc.cpp 中 checkf(false)），Windows 未实现，调用会断言崩溃，
	 * 因此这里使用 Core 始终可用的 FSHA1，并在清单中显式记录算法名，
	 * 避免下游把该摘要误当成 SHA-256。算法为 sha1，用于批次取证与篡改检测。
	 */
	bool HashFile(const FString& Path, FString& OutHex, FString& OutError)
	{
		TArray<uint8> Bytes;
		if (!FFileHelper::LoadFileToArray(Bytes, *Path))
		{
			OutError = FString::Printf(TEXT("无法读取待哈希文件：%s"), *Path);
			return false;
		}
		const FSHAHash Digest = FSHA1::HashBuffer(Bytes.GetData(), Bytes.Num());
		OutHex = Digest.ToString().ToLower();
		return true;
	}

	/** 导出插件版本来自 uplugin 描述符；缺失时留空而不是编造。 */
	FString PluginVersion()
	{
		const TSharedPtr<IPlugin> Plugin = IPluginManager::Get().FindPlugin(TEXT("AILocomotionDataset"));
		return Plugin.IsValid() ? Plugin->GetDescriptor().VersionName : FString();
	}

	bool ExportClip(UAnimSequence* Animation, const FString& Path, FScopedSlowTask& Progress, FString& Error, FString& OutSha1)
	{
		const UAILocomotionDatasetSettings* Settings = GetDefault<UAILocomotionDatasetSettings>();
		USkeleton* Skeleton = Animation->GetSkeleton();
		const IAnimationDataModel* DataModel = Animation->GetDataModel();
		if (!Skeleton || Animation->IsValidAdditive() || !DataModel || !DataModel->GetFrameRate().IsValid())
		{
			Error = LOCTEXT("InvalidAnimation", "动画必须有关联骨架、为非叠加动画，且源动画数据模型具有有效帧率。").ToString();
			return false;
		}
		const double Duration = Animation->GetPlayLength();
		const FFrameRate SourceRate = DataModel->GetFrameRate();
		const int32 FrameCount = DataModel->GetNumberOfKeys();
		if (!FMath::IsFinite(Duration) || FrameCount < 2 || FrameCount > Settings->MaxFramesPerClip || FrameCount > 18000)
		{
			Error = LOCTEXT("InvalidDuration", "动画长度超出采样范围，请检查单片段最大帧数。").ToString();
			return false;
		}
		const FReferenceSkeleton& RefSkeleton = Skeleton->GetReferenceSkeleton();
		const int32 RootIndex = RefSkeleton.FindBoneIndex(Settings->RootBone);
		const int32 PelvisIndex = RefSkeleton.FindBoneIndex(Settings->PelvisBone);
		if (RootIndex == INDEX_NONE || PelvisIndex == INDEX_NONE)
		{
			Error = FText::Format(LOCTEXT("MissingRootOrPelvis", "骨架缺少配置的 UE Root {0} 或骨盆 {1}，请在项目设置中指定正确骨骼。"), FText::FromName(Settings->RootBone), FText::FromName(Settings->PelvisBone)).ToString();
			return false;
		}
		for (int32 AncestorIndex = PelvisIndex; AncestorIndex != INDEX_NONE; AncestorIndex = RefSkeleton.GetParentIndex(AncestorIndex))
		{
			if (AncestorIndex == RootIndex)
			{
				break;
			}
			if (RefSkeleton.GetParentIndex(AncestorIndex) == INDEX_NONE)
			{
				Error = LOCTEXT("RootNotPelvisAncestor", "配置的 UE Root 必须是骨盆的祖先，不能从无关骨骼建立相对姿态。").ToString();
				return false;
			}
		}
		TArray<FName> Names;
		TArray<int32> SourceIndices;
		TArray<int32> Parents;
		for (int32 Index = 0; Index < RefSkeleton.GetNum(); ++Index)
		{
			const int32 Parent = RefSkeleton.GetParentIndex(Index);
			if (Index == PelvisIndex || SourceIndices.Contains(Parent))
			{
				Parents.Add(Index == PelvisIndex ? INDEX_NONE : SourceIndices.IndexOfByKey(Parent));
				SourceIndices.Add(Index);
				Names.Add(RefSkeleton.GetBoneName(Index));
			}
		}
		const TArray<FName> Required = { Settings->LeftHipBone, Settings->RightHipBone, Settings->LeftFootBone, Settings->LeftToeBone, Settings->RightFootBone, Settings->RightToeBone, Settings->LeftHandBone, Settings->RightHandBone };
		// JSON 在内存中构建；长片段可显式提高预算，但保留默认限额和有限的硬上限。
		int32 MaxBoneSamples = 250000;
		FParse::Value(FCommandLine::Get(), TEXT("AILocomotionMaxBoneSamples="), MaxBoneSamples);
		if (MaxBoneSamples < 250000 || MaxBoneSamples > 4000000)
		{
			Error = LOCTEXT("InvalidSampleBudget", "AILocomotionMaxBoneSamples 必须在 250000 到 4000000 之间。").ToString();
			return false;
		}
		if (Names.IsEmpty() || Names.Num() > 512 || int64(FrameCount) * Names.Num() > MaxBoneSamples)
		{
			Error = FText::Format(LOCTEXT("InvalidSkeleton", "骨骼数超过 512，或帧数乘骨骼数超过 {0}；可通过 AILocomotionMaxBoneSamples 显式调整导出预算。"), FText::AsNumber(MaxBoneSamples)).ToString();
			return false;
		}
		TSet<FName> RoleNames;
		for (FName Name : Required)
		{
			if (!Names.Contains(Name))
			{
				Error = FText::Format(LOCTEXT("MissingBone", "身体骨架缺少 {0}，请在项目设置中指定正确骨骼。"), FText::FromName(Name)).ToString();
				return false;
			}
			if (RoleNames.Contains(Name))
			{
				Error = FText::Format(LOCTEXT("DuplicateRole", "骨骼 {0} 被重复分配；左右髋和四个足部接触点必须使用不同骨骼。"), FText::FromName(Name)).ToString();
				return false;
			}
			RoleNames.Add(Name);
		}
		FAnimPose ReferencePose;
		UAnimPoseExtensions::GetReferencePose(Skeleton, ReferencePose);
		if (!ReferencePose.IsValid())
		{
			Error = LOCTEXT("InvalidReference", "无法读取骨架参考姿态。").ToString();
			return false;
		}
		TSharedRef<FJsonObject> Document = MakeShared<FJsonObject>();
		Document->SetNumberField(TEXT("schema_version"), 2);
		Document->SetStringField(TEXT("asset"), Animation->GetPathName());
		Document->SetStringField(TEXT("skeleton"), Skeleton->GetPathName());
		Document->SetStringField(TEXT("coordinate_system"), TEXT("unreal_root_relative_cm_xyzw"));
		Document->SetStringField(TEXT("root_policy"), TEXT("separate_authoritative"));
		Document->SetStringField(TEXT("pose_policy"), TEXT("pelvis_subtree_relative_to_root"));
		Document->SetStringField(TEXT("root_bone"), Settings->RootBone.ToString());
		Document->SetStringField(TEXT("pelvis_bone"), Settings->PelvisBone.ToString());
		Document->SetNumberField(TEXT("fps"), SourceRate.AsDecimal());
		Document->SetNumberField(TEXT("source_fps_numerator"), SourceRate.Numerator);
		Document->SetNumberField(TEXT("source_fps_denominator"), SourceRate.Denominator);
		Document->SetStringField(TEXT("sampling_policy"), TEXT("source_data_keys"));
		TArray<TSharedPtr<FJsonValue>> SampleTimes;
		for (int32 FrameIndex = 0; FrameIndex < FrameCount; ++FrameIndex)
		{
			SampleTimes.Add(MakeShared<FJsonValueNumber>(FMath::Min(FrameIndex / SourceRate.AsDecimal(), Duration)));
		}
		Document->SetArrayField(TEXT("timestamps_seconds"), SampleTimes);
		Document->SetNumberField(TEXT("duration_seconds"), Duration);
		TArray<TSharedPtr<FJsonValue>> Bones;
		const FTransform& RootReferenceTransform = UAnimPoseExtensions::GetRefBonePose(ReferencePose, Settings->RootBone, EAnimPoseSpaces::World);
		if (RootReferenceTransform.ContainsNaN() || !RootReferenceTransform.GetScale3D().Equals(FVector::OneVector, 0.0001))
		{
			Error = LOCTEXT("InvalidRootReferenceTransform", "UE Root 参考姿态包含非单位缩放或无效变换，请先规范化骨架。").ToString();
			return false;
		}
		for (int32 Index = 0; Index < Names.Num(); ++Index)
		{
			const FTransform Transform = UAnimPoseExtensions::GetRefBonePose(ReferencePose, Names[Index], EAnimPoseSpaces::World).GetRelativeTransform(RootReferenceTransform);
			if (Transform.ContainsNaN() || !Transform.GetScale3D().Equals(FVector::OneVector, 0.0001))
			{
				Error = LOCTEXT("InvalidReferenceTransform", "参考姿态包含非单位缩放或无效变换，请先规范化骨架。").ToString();
				return false;
			}
			const FVector Position = Transform.GetTranslation();
			const FQuat Rotation = Transform.GetRotation();
			TSharedRef<FJsonObject> Bone = MakeShared<FJsonObject>();
			Bone->SetStringField(TEXT("name"), Names[Index].ToString());
			Bone->SetNumberField(TEXT("parent"), Parents[Index]);
			Bone->SetArrayField(TEXT("position"), Numbers({ Position.X, Position.Y, Position.Z }));
			Bone->SetArrayField(TEXT("rotation"), Numbers({ Rotation.X, Rotation.Y, Rotation.Z, Rotation.W }));
			Bones.Add(MakeShared<FJsonValueObject>(Bone));
		}
		Document->SetArrayField(TEXT("bones"), Bones);
		TSharedRef<FJsonObject> Roles = MakeShared<FJsonObject>();
		Roles->SetStringField(TEXT("left_hip"), Settings->LeftHipBone.ToString());
		Roles->SetStringField(TEXT("right_hip"), Settings->RightHipBone.ToString());
		Roles->SetStringField(TEXT("left_foot"), Settings->LeftFootBone.ToString());
		Roles->SetStringField(TEXT("right_foot"), Settings->RightFootBone.ToString());
		Roles->SetStringField(TEXT("left_toe"), Settings->LeftToeBone.ToString());
		Roles->SetStringField(TEXT("right_toe"), Settings->RightToeBone.ToString());
		Roles->SetStringField(TEXT("left_hand"), Settings->LeftHandBone.ToString());
		Roles->SetStringField(TEXT("right_hand"), Settings->RightHandBone.ToString());
		Document->SetObjectField(TEXT("roles"), Roles);

		// 使用原始求值保留完整 Root 轨道，再将身体变换改写到 Root 局部空间；两套数据不能在求值阶段提前混合。
		FAnimPoseEvaluationOptions Options;
		Options.EvaluationType = EAnimDataEvalType::Raw;
		Options.bShouldRetarget = false;
		Options.bExtractRootMotion = false;
		Options.bIncorporateRootMotionIntoPose = true;
		Options.bEvaluateCurves = false;
		TArray<TSharedPtr<FJsonValue>> Frames;
		TArray<TSharedPtr<FJsonValue>> RootFrames;
		for (int32 FrameIndex = 0; FrameIndex < FrameCount; ++FrameIndex)
		{
			Progress.EnterProgressFrame(1.0f / static_cast<float>(FrameCount));
			if (Progress.ShouldCancel())
			{
				Error = LOCTEXT("Cancelled", "导出已取消；未发布训练清单。").ToString();
				return false;
			}
			FAnimPose Pose;
			UAnimPoseExtensions::GetAnimPoseAtTime(Animation, FMath::Min(FrameIndex / SourceRate.AsDecimal(), Duration), Options, Pose);
			if (!Pose.IsValid())
			{
				Error = LOCTEXT("InvalidPose", "动画姿态求值失败。").ToString();
				return false;
			}
			TArray<FName> EvaluatedNames;
			UAnimPoseExtensions::GetBoneNames(Pose, EvaluatedNames);
			const TSet<FName> EvaluatedNameSet(EvaluatedNames);
			if (!EvaluatedNameSet.Contains(Settings->RootBone))
			{
				Error = FText::Format(LOCTEXT("MissingEvaluatedRoot", "动画姿态缺少 UE Root 骨骼 {0}，无法分离权威轨道。"), FText::FromName(Settings->RootBone)).ToString();
				return false;
			}
			const FTransform& RootTransform = UAnimPoseExtensions::GetBonePose(Pose, Settings->RootBone, EAnimPoseSpaces::World);
			if (RootTransform.ContainsNaN() || !RootTransform.GetScale3D().Equals(FVector::OneVector, 0.0001))
			{
				Error = LOCTEXT("UnsupportedRootPose", "UE Root 包含无效数值或缩放；当前权威轨道要求刚性变换。").ToString();
				return false;
			}
			const FVector RootPosition = RootTransform.GetTranslation();
			const FQuat RootRotation = RootTransform.GetRotation();
			RootFrames.Add(MakeShared<FJsonValueArray>(Numbers({ RootPosition.X, RootPosition.Y, RootPosition.Z, RootRotation.X, RootRotation.Y, RootRotation.Z, RootRotation.W })));
			TArray<TSharedPtr<FJsonValue>> Transforms;
			for (FName Name : Names)
			{
				if (!EvaluatedNameSet.Contains(Name))
				{
					Error = FText::Format(LOCTEXT("MissingEvaluatedBone", "动画姿态缺少骨骼 {0}，无法导出完整身体姿态。"), FText::FromName(Name)).ToString();
					return false;
				}
				const FTransform Transform = UAnimPoseExtensions::GetBonePose(Pose, Name, EAnimPoseSpaces::World).GetRelativeTransform(RootTransform);
				if (Transform.ContainsNaN() || !Transform.GetScale3D().Equals(FVector::OneVector, 0.0001))
				{
					Error = LOCTEXT("UnsupportedPose", "动画缺少骨骼、包含无效数值或缩放；当前训练表示要求刚性骨骼。").ToString();
					return false;
				}
				const FVector Position = Transform.GetTranslation();
				const FQuat Rotation = Transform.GetRotation();
				Transforms.Add(MakeShared<FJsonValueArray>(Numbers({ Position.X, Position.Y, Position.Z, Rotation.X, Rotation.Y, Rotation.Z, Rotation.W })));
			}
			Frames.Add(MakeShared<FJsonValueArray>(Transforms));
		}
		Document->SetArrayField(TEXT("frames"), Frames);
		Document->SetArrayField(TEXT("root_frames"), RootFrames);
		if (!WriteJson(Path, Document))
		{
			Error = LOCTEXT("WriteFailed", "无法写入动画数据文件，请检查输出目录和磁盘空间。").ToString();
			return false;
		}
		// 写出后立即回读计算摘要：清单记录的是磁盘上的真实字节，不是预期内容。
		if (!HashFile(Path, OutSha1, Error))
		{
			return false;
		}
		return true;
	}

	bool ExportAssets(const TArray<FAssetData>& Assets, bool bContinueAfterFailure, FString& OutDirectory, FString& OutError)
	{
		OutDirectory = FPaths::ConvertRelativePathToFull(FPaths::ProjectSavedDir() / TEXT("AILocomotionDataset") / FGuid::NewGuid().ToString(EGuidFormats::Digits));
		IFileManager::Get().MakeDirectory(*OutDirectory, true);
		FScopedSlowTask Progress(Assets.Num(), LOCTEXT("Exporting", "正在导出动画训练数据"));
		if (!IsRunningCommandlet() && !FApp::IsUnattended())
		{
			Progress.MakeDialog(true);
		}

		TArray<TSharedPtr<FJsonValue>> Files;
		TArray<TSharedPtr<FJsonValue>> Clips;
		TArray<TSharedPtr<FJsonValue>> Failures;
		for (int32 Index = 0; Index < Assets.Num(); ++Index)
		{
			UAnimSequence* Animation = Cast<UAnimSequence>(Assets[Index].GetAsset());
			const FString Filename = FString::Printf(TEXT("clip_%05d.json"), Files.Num());
			FString Error;
			FString Sha1;
			if (!Animation || !ExportClip(Animation, OutDirectory / Filename, Progress, Error, Sha1))
			{
				if (!Animation)
				{
					Error = LOCTEXT("WrongAsset", "选中资产包含非 AnimSequence 类型。").ToString();
				}
				if (!bContinueAfterFailure)
				{
					OutError = Assets[Index].GetObjectPathString() + TEXT("\n") + Error;
					return false;
				}
				TSharedRef<FJsonObject> Failure = MakeShared<FJsonObject>();
				Failure->SetStringField(TEXT("asset"), Assets[Index].GetObjectPathString());
				Failure->SetStringField(TEXT("error"), Error);
				Failures.Add(MakeShared<FJsonValueObject>(Failure));
				UE_LOG(LogTemp, Warning, TEXT("AI Locomotion 数据导出跳过 %s：%s"), *Assets[Index].GetObjectPathString(), *Error);
				continue;
			}
			Files.Add(MakeShared<FJsonValueString>(Filename));
			// 逐片段内容身份：文件摘要绑定来源资产，使磁盘文件可被取证与比对。
			TSharedRef<FJsonObject> Clip = MakeShared<FJsonObject>();
			Clip->SetStringField(TEXT("file"), Filename);
			Clip->SetStringField(TEXT("asset"), Assets[Index].GetObjectPathString());
			Clip->SetStringField(TEXT("sha1"), Sha1);
			Clips.Add(MakeShared<FJsonValueObject>(Clip));
		}

		if (Files.IsEmpty())
		{
			OutError = LOCTEXT("NoExportedClips", "没有动画通过导出校验，未发布训练清单。请检查骨架配置和日志。").ToString();
			return false;
		}

		TSharedRef<FJsonObject> Manifest = MakeShared<FJsonObject>();
		Manifest->SetNumberField(TEXT("schema_version"), 2);
		Manifest->SetStringField(TEXT("training_contract"), TEXT("pose_only_root_authoritative"));
		// clips 保持既有字符串列表契约，追加 clip_identity 记录内容身份，不破坏既有消费者。
		Manifest->SetArrayField(TEXT("clips"), Files);
		Manifest->SetArrayField(TEXT("clip_identity"), Clips);
		// 批次身份：此前只有随机 GUID 目录名，无法判断批次由哪个引擎/插件产出。
		Manifest->SetStringField(TEXT("engine_version"), FEngineVersion::Current().ToString(EVersionComponent::Patch));
		Manifest->SetStringField(TEXT("plugin_name"), TEXT("AILocomotionDataset"));
		Manifest->SetStringField(TEXT("plugin_version"), PluginVersion());
		Manifest->SetStringField(TEXT("hash_algorithm"), TEXT("sha1"));
		Manifest->SetStringField(TEXT("exported_at"), FDateTime::UtcNow().ToIso8601());
		TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>();
		Report->SetNumberField(TEXT("candidate_anim_sequences"), Assets.Num());
		Report->SetNumberField(TEXT("exported_clips"), Files.Num());
		Report->SetArrayField(TEXT("skipped_clips"), Failures);
		if (!WriteJson(OutDirectory / TEXT("batch_report.json"), Report))
		{
			OutError = LOCTEXT("ReportFailed", "批次报告写入失败。").ToString();
			return false;
		}

		// 清单是批次发布标志；报告失败时不能留下可被预处理接受的完整批次。
		if (!WriteJson(OutDirectory / TEXT("manifest.json"), Manifest))
		{
			OutError = LOCTEXT("ManifestFailed", "清单写入失败，批次未完成。").ToString();
			return false;
		}

		return true;
	}

	void ExportSelected()
	{
		TArray<FAssetData> Assets;
		FModuleManager::LoadModuleChecked<FContentBrowserModule>(TEXT("ContentBrowser")).Get().GetSelectedAssets(Assets);
		if (Assets.IsEmpty())
		{
			FMessageDialog::Open(EAppMsgType::Ok, LOCTEXT("NoSelection", "请先在内容浏览器中选择 AnimSequence 动画资产。"));
			return;
		}
		FString Directory;
		FString Error;
		if (!ExportAssets(Assets, false, Directory, Error))
		{
			FMessageDialog::Open(EAppMsgType::Ok, FText::FromString(Error));
			return;
		}
		FMessageDialog::Open(EAppMsgType::Ok, FText::Format(LOCTEXT("Complete", "动画采样完成，输出目录：\n{0}\n下一步使用 prepare_unreal_dataset.py 生成训练特征。"), FText::FromString(Directory)));
	}

	void ExportAnimSequencesInPath(const FString& AssetPath)
	{
		FAssetRegistryModule& AssetRegistryModule = FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry"));
		IAssetRegistry& AssetRegistry = AssetRegistryModule.Get();
		AssetRegistry.WaitForCompletion();

		TArray<FAssetData> AllAssets;
		AssetRegistry.GetAssetsByPath(*AssetPath, AllAssets, true, true);
		TArray<FAssetData> Animations;
		const FTopLevelAssetPath AnimSequenceClassPath = UAnimSequence::StaticClass()->GetClassPathName();
		for (const FAssetData& Asset : AllAssets)
		{
			if (Asset.AssetClassPath == AnimSequenceClassPath)
			{
				Animations.Add(Asset);
			}
		}
		Animations.Sort([](const FAssetData& Left, const FAssetData& Right) { return Left.GetObjectPathString() < Right.GetObjectPathString(); });

		FString Directory;
		FString Error;
		if (!ExportAssets(Animations, true, Directory, Error))
		{
			UE_LOG(LogTemp, Error, TEXT("AI Locomotion 批量导出失败：%s"), *Error);
			return;
		}
		UE_LOG(LogTemp, Display, TEXT("AI Locomotion 批量导出完成：候选 %d 段，输出 %s"), Animations.Num(), *Directory);
	}
}

/** 菜单注册由模块生命周期持有，卸载时解除所有回调。 */
class FAILocomotionDatasetEditorModule : public IModuleInterface
{
public:
	virtual void StartupModule() override
	{
		UToolMenus::RegisterStartupCallback(FSimpleMulticastDelegate::FDelegate::CreateRaw(this, &FAILocomotionDatasetEditorModule::RegisterMenus));
		FString AssetPath;
		if (FParse::Value(FCommandLine::Get(), TEXT("AILocomotionExportPath="), AssetPath))
		{
			ExportTickerHandle = FTSTicker::GetCoreTicker().AddTicker(TEXT("AILocomotionDataset.ExportPath"), 0.0f, [AssetPath](float) {
				ExportAnimSequencesInPath(AssetPath);
				RequestEngineExit(TEXT("AI Locomotion 批量导出完成"));
				return false;
			});
		}
	}

	virtual void ShutdownModule() override
	{
		FTSTicker::RemoveTicker(ExportTickerHandle);
		UToolMenus::UnRegisterStartupCallback(this);
		UToolMenus::UnregisterOwner(this);
	}

private:
	FTSTicker::FDelegateHandle ExportTickerHandle;

	void RegisterMenus()
	{
		FToolMenuOwnerScoped Owner(this);
		UToolMenu* Menu = UToolMenus::Get()->ExtendMenu(TEXT("LevelEditor.MainMenu.Tools"));
		FToolMenuSection& Section = Menu->FindOrAddSection(TEXT("AILocomotion"));
		Section.AddMenuEntry(TEXT("ExportAILocomotionDataset"), LOCTEXT("Export", "AI Locomotion：导出选中动画"),
			LOCTEXT("ExportTooltip", "读取内容浏览器选中的 AnimSequence，导出组件空间骨骼姿态与骨架信息。"), FSlateIcon(), FUIAction(FExecuteAction::CreateStatic(&ExportSelected)));
	}
};

IMPLEMENT_MODULE(FAILocomotionDatasetEditorModule, AILocomotionDatasetEditor)

#undef LOCTEXT_NAMESPACE
