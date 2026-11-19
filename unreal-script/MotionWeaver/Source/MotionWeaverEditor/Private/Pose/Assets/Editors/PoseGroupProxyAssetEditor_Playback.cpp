// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Assets/Editors/PoseGroupProxyAssetEditor.h"

#include "Core/MotionWeaverLog.h"
#include "Pose/Data/PoseGroupProxyAsset.h"
#include "Pose/Data/PoseProxyAsset.h"
#include "Pose/Viewport/SPoseGroupViewport.h"
#include "RefPose/RefPoseGroupGenerator.h"
#include "RefPose/RefPoseModel.h"

#include "HAL/FileManager.h"
#include "HAL/PlatformMisc.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"

#define LOCTEXT_NAMESPACE "FPoseGroupProxyAssetEditorPlayback"

namespace
{
	FString NormalizePackageDirectory(FString In)
	{
		In.TrimStartAndEndInline();
		if (In.Len() >= 2 && In.StartsWith(TEXT("\"")) && In.EndsWith(TEXT("\"")))
		{
			In = In.Mid(1, In.Len() - 2);
			In.TrimStartAndEndInline();
		}
		return In;
	}

	FString GetSavedPackageOverrideFile()
	{
		return FPaths::ProjectSavedDir() / TEXT("MotionWeaver/RefPosePackage.txt");
	}

	FString ReadSavedPackageOverride()
	{
		const FString File = GetSavedPackageOverrideFile();
		FString Text;
		if (!FFileHelper::LoadFileToString(Text, *File))
		{
			return FString();
		}
		return NormalizePackageDirectory(Text);
	}

	/**
	 * 模型包定位。
	 *
	 * 优先级：环境变量（自动化 / 临时覆盖） > 工程 Saved 下的本地覆盖文件。
	 * 不把任何用户目录写进代码：换机器或换包时只改这两个入口之一。
	 */
	bool ResolvePackageDirectory(FString& OutDir, FString& OutSource, FString& OutError)
	{
		const FString Env = NormalizePackageDirectory(
			FPlatformMisc::GetEnvironmentVariable(TEXT("MW_REFPOSE_UE_PACKAGE")));
		if (!Env.IsEmpty())
		{
			OutDir = Env;
			OutSource = TEXT("环境变量 MW_REFPOSE_UE_PACKAGE");
			return true;
		}

		const FString Saved = ReadSavedPackageOverride();
		if (!Saved.IsEmpty())
		{
			OutDir = Saved;
			OutSource = FString::Printf(
				TEXT("本地覆盖文件 %s"), *GetSavedPackageOverrideFile());
			return true;
		}

		OutError = FString::Printf(
			TEXT("未配置 RefPose 模型包。请任选一种方式：\n")
			TEXT("1) 设置环境变量 MW_REFPOSE_UE_PACKAGE；\n")
			TEXT("2) 把包目录写进 %s。\n")
			TEXT("目录需包含 model.onnx / skeleton.json / stats.bin / manifest.json。"),
			*GetSavedPackageOverrideFile());
		return false;
	}
}

void FPoseGroupProxyAssetEditor::MarkGenerationStale()
{
	if (GeneratedFrames.Num() > 0)
	{
		bGenerationStale = true;
		// 过期结果不能再继续播放：状态文字说「已过期」，画面却还在动，
		// 作者会误以为看到的是当前资产。暂停后由「生成并播放」显式重建。
		bIsPlaying = false;
		if (ViewportWidget.IsValid())
		{
			// 回到关键帧视图，避免把旧结果当成当前资产继续看。
			ViewportWidget->SetPlaybackVisible(false);
		}
		GenerationStatus = LOCTEXT("StaleStatus", "结果已过期：资产已改动，请重新生成。").ToString();
	}
}

void FPoseGroupProxyAssetEditor::GenerateAndPlay()
{
	GenerationWarnings.Reset();
	bLastGenerationFailed = false;
	UE_LOG(LogMotionWeaver, Display,
		TEXT("[RefPose] 开始生成：资产=%s，关键帧=%d"),
		GroupAsset != nullptr ? *GroupAsset->GetName() : TEXT("<none>"),
		GroupAsset != nullptr ? GroupAsset->Keys.Num() : 0);

	FString Error;
	if (!BuildAndGenerate(Error))
	{
		bLastGenerationFailed = true;
		GenerationStatus = FString::Printf(TEXT("生成失败：%s"), *Error);
		UE_LOG(LogMotionWeaver, Error, TEXT("[RefPose] 生成失败：%s"), *Error);
		bIsPlaying = false;
		if (ViewportWidget.IsValid())
		{
			ViewportWidget->ClearPlaybackFrames();
		}
		return;
	}

	bGenerationStale = false;
	PlaybackTimeSeconds = 0.0;
	bIsPlaying = true;

	if (ViewportWidget.IsValid())
	{
		ViewportWidget->SetPlaybackFrames(GeneratedFrames);
		ViewportWidget->SetPlaybackFrame(0);
		ViewportWidget->SetPlaybackVisible(true);
	}

	GenerationStatus = FString::Printf(
		TEXT("已生成 %d 帧 / 推理 %.2f ms（峰值 %.2f ms）/ 骨长偏差 %.4f cm / 硬参考 %d 帧 / 未映射 %d 骨"),
		GenerationMetrics.FrameCount,
		GenerationMetrics.TotalInferenceMs,
		GenerationMetrics.MaxInferenceMs,
		GenerationMetrics.MaxBoneLengthDeviationCm,
		GenerationMetrics.HardReferenceFrames,
		UnmappedBoneCount);

	for (const FString& Warning : GenerationWarnings)
	{
		UE_LOG(LogMotionWeaver, Warning, TEXT("[RefPose] 轨迹告警：%s"), *Warning);
	}
	if (GenerationWarnings.Num() > 0)
	{
		GenerationStatus += FString::Printf(
			TEXT("｜轨迹告警 %d 条：%s%s"),
			GenerationWarnings.Num(),
			*GenerationWarnings[0],
			GenerationWarnings.Num() > 1 ? TEXT("（其余见日志）") : TEXT(""));
	}

	UE_LOG(LogMotionWeaver, Display,
		TEXT("[RefPose] 生成完成：%d 帧，推理 %.2f ms，未映射 %d 骨，告警 %d 条"),
		GenerationMetrics.FrameCount,
		GenerationMetrics.TotalInferenceMs,
		UnmappedBoneCount,
		GenerationWarnings.Num());
}

bool FPoseGroupProxyAssetEditor::BuildAndGenerate(FString& OutError)
{
	GeneratedFrames.Reset();
	GenerationMetrics = FRefPoseGenerationMetrics();
	UnmappedBoneCount = 0;

	if (!EnsureModelAsset(OutError))
	{
		return false;
	}
	if (GroupAsset == nullptr)
	{
		OutError = TEXT("没有打开的姿态组资产。");
		return false;
	}

	// 每次生成都按当前关键帧与设置重解一次。Result 是 Transient 缓存，
	// 撤销/重做、细节面板嵌套属性等路径未必都能保证它是最新的；
	// 带着旧轨迹去推理，表现就是「改了参数但还按旧参数跑」。
	GroupAsset->Solve();
	const FChainDiagnostics& Diagnostics = GroupAsset->GetDiagnostics();
	UE_LOG(LogMotionWeaver, Display,
		TEXT("[RefPose] 求解：关键帧 %d，%.3f fps，速度上限 %.0f cm/s，样本 %d（%d..%d），%s"),
		GroupAsset->Keys.Num(), GroupAsset->Settings.FrameRate,
		GroupAsset->Settings.MaxSpeedCmPerSecond,
		GroupAsset->GetSamples().Num(), Diagnostics.FirstFrame, Diagnostics.LastFrame,
		Diagnostics.IsValid() ? TEXT("通过") : TEXT("有告警"));
	for (int32 KeyIndex = 0; KeyIndex < GroupAsset->Keys.Num(); ++KeyIndex)
	{
		const FPoseKey& Key = GroupAsset->Keys[KeyIndex];
		UE_LOG(LogMotionWeaver, Display,
			TEXT("[RefPose] 关键帧 %d：帧 %d（%.3f s），位置 (%.1f, %.1f, %.1f)，姿态 %s"),
			KeyIndex, Key.Frame,
			GroupAsset->Settings.FrameRate > 0.0
				? Key.Frame / GroupAsset->Settings.FrameRate : 0.0,
			Key.LocalLocation.X, Key.LocalLocation.Y, Key.LocalLocation.Z,
			Key.PoseAsset != nullptr ? *Key.PoseAsset->GetName() : TEXT("<空>"));
	}

	FRefPoseGroupGenerationResult Result;
	if (!FRefPoseGroupGenerator::Generate(*GroupAsset, *ModelAsset, Result, OutError))
	{
		return false;
	}

	GeneratedFrames = MoveTemp(Result.Frames);
	GenerationMetrics = Result.Metrics;
	UnmappedBoneCount = Result.UnmappedBoneCount;
	GenerationWarnings = MoveTemp(Result.Warnings);
	return true;
}

bool FPoseGroupProxyAssetEditor::EnsureModelAsset(FString& OutError)
{
	FString PackageDir;
	FString PackageSource;
	if (!ResolvePackageDirectory(PackageDir, PackageSource, OutError))
	{
		return false;
	}
	PackageDir = FPaths::ConvertRelativePathToFull(PackageDir);

	// 同一路径只解析一次；但作者改了 RefPosePackage.txt 或环境变量后，
	// 下一次生成必须换到新包，否则表现又是「改了不生效」。
	if (ModelAsset.IsValid() && ModelAsset->HasUsableContract()
		&& LoadedPackageDir == PackageDir)
	{
		return true;
	}

	if (!IFileManager::Get().DirectoryExists(*PackageDir))
	{
		OutError = FString::Printf(
			TEXT("模型包目录不存在：%s（来源：%s）"), *PackageDir, *PackageSource);
		return false;
	}
	UE_LOG(LogMotionWeaver, Display,
		TEXT("[RefPose] 模型包目录：%s（来源：%s）"), *PackageDir, *PackageSource);

	URefPoseModelAsset* Loaded = URefPoseModelAsset::CreateFromOnnxFile(
		PackageDir / TEXT("model.onnx"), OutError);
	if (Loaded == nullptr)
	{
		return false;
	}
	if (!Loaded->LoadContractFromPackage(PackageDir, OutError))
	{
		return false;
	}

	UE_LOG(LogMotionWeaver, Display,
		TEXT("[RefPose] 模型已加载：%d 骨，窗口 %d+%d，帧率 %.4f"),
		Loaded->BoneCount, Loaded->HistoryFrames, Loaded->FutureFrames, Loaded->FrameRate);

	// TStrongObjectPtr 是编辑器（非 UObject）持有的 GC 根；
	// 下次生成直接复用，避免重复解析 ONNX 与统计量。
	ModelAsset.Reset(Loaded);
	LoadedPackageDir = PackageDir;
	return true;
}

void FPoseGroupProxyAssetEditor::TogglePlayback()
{
	if (GeneratedFrames.Num() == 0 || bGenerationStale)
	{
		GenerateAndPlay();
		return;
	}
	if (!bIsPlaying)
	{
		// 播完会停在末帧；此时按「继续」应当从头重播，而不是立刻又停在末帧。
		const double FramesPerSecond = FMath::Max(
			1.0, static_cast<double>(GetPlaybackFramesPerSecond()));
		if (PlaybackTimeSeconds * FramesPerSecond >= GeneratedFrames.Num())
		{
			PlaybackTimeSeconds = 0.0;
		}
	}
	bIsPlaying = !bIsPlaying;
	if (bIsPlaying && ViewportWidget.IsValid())
	{
		// 停止会退出播放预览；继续播放要重新进入，否则只推进了数据、画面没回来。
		ViewportWidget->SetPlaybackVisible(true);
	}
}

void FPoseGroupProxyAssetEditor::StopPlayback()
{
	bIsPlaying = false;
	PlaybackTimeSeconds = 0.0;
	if (ViewportWidget.IsValid())
	{
		if (ViewportWidget->HasPlaybackFrames())
		{
			ViewportWidget->SetPlaybackFrame(0);
		}
		// 停止 = 退出播放预览，回到可编辑的关键帧视图。
		ViewportWidget->SetPlaybackVisible(false);
	}
}

float FPoseGroupProxyAssetEditor::GetPlaybackFramesPerSecond() const
{
	return ModelAsset.IsValid()
		? static_cast<float>(ModelAsset->FrameRate)
		: 120.0f;
}

bool FPoseGroupProxyAssetEditor::TickPlayback(float DeltaSeconds)
{
	if (!bIsPlaying || GeneratedFrames.Num() == 0 || !ViewportWidget.IsValid())
	{
		return false;
	}

	PlaybackTimeSeconds += DeltaSeconds;
	const double FramesPerSecond =
		FMath::Max(1.0, static_cast<double>(GetPlaybackFramesPerSecond()));
	const int32 FrameIndex = static_cast<int32>(PlaybackTimeSeconds * FramesPerSecond);
	if (FrameIndex >= GeneratedFrames.Num())
	{
		// 播完停在末帧，不自动循环：首版先让作者看清结尾有没有跳。
		bIsPlaying = false;
		ViewportWidget->SetPlaybackFrame(GeneratedFrames.Num() - 1);
		return true;
	}
	ViewportWidget->SetPlaybackFrame(FrameIndex);
	return true;
}


bool FPoseGroupProxyAssetEditor::TickPlaybackTick(float DeltaSeconds)
{
	// 返回值控制 ticker 是否继续存活；编辑器关闭时已在 OnClose 里移除。
	TickPlayback(DeltaSeconds);
	return true;
}

FString FPoseGroupProxyAssetEditor::GetPlaybackStatusText() const
{
	if (GeneratedFrames.Num() == 0)
	{
		return GenerationStatus.IsEmpty()
			? TEXT("未生成。可从「生成并播放」开始。")
			: GenerationStatus;
	}
	if (bGenerationStale)
	{
		return GenerationStatus.IsEmpty()
			? TEXT("结果已过期，请重新生成。")
			: GenerationStatus;
	}

	const int32 CurrentFrame = ViewportWidget.IsValid()
		? ViewportWidget->GetPlaybackFrameIndex()
		: INDEX_NONE;
	const double FramesPerSecond =
		FMath::Max(1.0, static_cast<double>(GetPlaybackFramesPerSecond()));
	const FString FrameText = CurrentFrame >= 0
		? FString::Printf(
			TEXT("%d/%d 帧 · %.2f/%.2f s"),
			CurrentFrame + 1,
			GeneratedFrames.Num(),
			CurrentFrame / FramesPerSecond,
			GeneratedFrames.Num() / FramesPerSecond)
		: FString::Printf(
			TEXT("%d 帧 · %.2f s"),
			GeneratedFrames.Num(),
			GeneratedFrames.Num() / FramesPerSecond);

	return FString::Printf(TEXT("%s｜%s｜%s"),
		bIsPlaying ? TEXT("播放中") : TEXT("已暂停"),
		*FrameText,
		*GenerationStatus);
}

#undef LOCTEXT_NAMESPACE
