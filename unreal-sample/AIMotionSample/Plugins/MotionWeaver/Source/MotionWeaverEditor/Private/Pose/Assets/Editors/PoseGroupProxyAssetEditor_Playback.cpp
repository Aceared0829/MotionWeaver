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
#include "Dom/JsonObject.h"
#include "Misc/DateTime.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "ScopedTransaction.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

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
	 * 优先级：资产里显式填写的路径 > 环境变量（自动化 / 临时覆盖）
	 * > 工程 Saved 下的本地覆盖文件。
	 *
	 * 资产路径一旦显式填写就不再回退：路径不存在时直接报错。
	 * 静默回退会让作者以为「换了包」，实际还在跑旧权重。
	 * 不把任何用户目录写进代码：换机器或换包时只改这三个入口之一。
	 */
	bool ResolvePackageDirectory(
		const UPoseGroupProxyAsset* Group,
		FString& OutDir,
		FString& OutSource,
		FString& OutError)
	{
		if (Group != nullptr)
		{
			const FString AssetPath =
				NormalizePackageDirectory(Group->RefPoseGeneration.ModelPackagePath);
			if (!AssetPath.IsEmpty())
			{
				OutDir = AssetPath;
				OutSource = TEXT("资产设置 RefPoseGeneration.ModelPackagePath");
				return true;
			}
		}

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
			TEXT("1) 在时间轴的「模型包」里填写目录（存在资产上，优先级最高）；\n")
			TEXT("2) 设置环境变量 MW_REFPOSE_UE_PACKAGE；\n")
			TEXT("3) 把包目录写进 %s。\n")
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
		ViewportWidget->SetAuthorFrames(AuthorFrames);
		ViewportWidget->SetShowAuthorGhost(bShowAuthorGhost);
		ViewportWidget->SetPlaybackKeepKeyframes(bShowKeyframesDuringPlayback);
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
	AnchorAlignments.Reset();
	AnchorSkips.Reset();
	FrameSolvedFrames.Reset();
	FrameAuthorFrames.Reset();
	FrameMaxRotationStepDeg.Reset();
	FrameWorstRotationBoneIndex.Reset();
	FrameMaxPoseStepCm.Reset();
	FrameWorstPoseBoneIndex.Reset();
	AuthorFrames.Reset();
	FramePredictionErrorCm.Reset();
	SkeletonBoneNames.Reset();

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
	AnchorAlignments = MoveTemp(Result.AnchorAlignments);
	AnchorSkips = MoveTemp(Result.AnchorSkips);
	FrameSolvedFrames = MoveTemp(Result.FrameSolvedFrames);
	FrameAuthorFrames = MoveTemp(Result.FrameAuthorFrames);
	FrameMaxRotationStepDeg = MoveTemp(Result.FrameMaxRotationStepDeg);
	FrameWorstRotationBoneIndex = MoveTemp(Result.FrameWorstRotationBoneIndex);
	FrameMaxPoseStepCm = MoveTemp(Result.FrameMaxPoseStepCm);
	FrameWorstPoseBoneIndex = MoveTemp(Result.FrameWorstPoseBoneIndex);
	AuthorFrames = MoveTemp(Result.AuthorFrames);
	FramePredictionErrorCm = MoveTemp(Result.FramePredictionErrorCm);
	SkeletonBoneNames = MoveTemp(Result.SkeletonBoneNames);
	GenerationWarnings = MoveTemp(Result.Warnings);
	return true;
}

bool FPoseGroupProxyAssetEditor::EnsureModelAsset(FString& OutError)
{
	FString PackageDir;
	FString PackageSource;
	if (!ResolvePackageDirectory(GroupAsset, PackageDir, PackageSource, OutError))
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

void FPoseGroupProxyAssetEditor::ScrubPlayback(int32 FrameIndex)
{
	if (GeneratedFrames.Num() == 0)
	{
		return;
	}
	const int32 Clamped = FMath::Clamp(
		FrameIndex, 0, GeneratedFrames.Num() - 1);
	// 拖动即暂停：逐帧检查时画面不应继续跑。
	bIsPlaying = false;
	const double FramesPerSecond =
		FMath::Max(1.0, static_cast<double>(GetPlaybackFramesPerSecond()));
	PlaybackTimeSeconds = Clamped / FramesPerSecond;

	if (ViewportWidget.IsValid())
	{
		ViewportWidget->SetPlaybackKeepKeyframes(bShowKeyframesDuringPlayback);
		ViewportWidget->SetPlaybackFrame(Clamped);
		// 从「已停止」拖进度条时也要把播放预览重新显示出来。
		ViewportWidget->SetPlaybackVisible(true);
	}
}

void FPoseGroupProxyAssetEditor::StepPlayback(int32 Delta)
{
	if (GeneratedFrames.Num() == 0)
	{
		return;
	}
	const int32 Current = ViewportWidget.IsValid()
		&& ViewportWidget->IsPlaybackVisible()
		? ViewportWidget->GetPlaybackFrameIndex()
		: INDEX_NONE;
	const int32 Base = Current != INDEX_NONE
		? Current
		: (Delta >= 0 ? -1 : GeneratedFrames.Num());
	ScrubPlayback(Base + Delta);
}

void FPoseGroupProxyAssetEditor::JumpPlaybackToAdjacentKey(bool bNext)
{
	if (GroupAsset == nullptr
		|| GeneratedFrames.Num() == 0
		|| FrameAuthorFrames.Num() != GeneratedFrames.Num())
	{
		return;
	}

	const int32 Current = ViewportWidget.IsValid()
		&& ViewportWidget->IsPlaybackVisible()
		? ViewportWidget->GetPlaybackFrameIndex()
		: INDEX_NONE;
	const double CurrentAuthor = FrameAuthorFrames.IsValidIndex(Current)
		? FrameAuthorFrames[Current]
		: (bNext ? -TNumericLimits<double>::Max() : TNumericLimits<double>::Max());

	double Target = bNext
		? TNumericLimits<double>::Max()
		: -TNumericLimits<double>::Max();
	bool bFound = false;
	for (const FPoseKey& Key : GroupAsset->Keys)
	{
		if (bNext && Key.Frame > CurrentAuthor + 0.5)
		{
			Target = FMath::Min(Target, static_cast<double>(Key.Frame));
			bFound = true;
		}
		else if (!bNext && Key.Frame < CurrentAuthor - 0.5)
		{
			Target = FMath::Max(Target, static_cast<double>(Key.Frame));
			bFound = true;
		}
	}
	if (!bFound)
	{
		return;
	}

	int32 BestFrame = INDEX_NONE;
	double BestDistance = TNumericLimits<double>::Max();
	for (int32 FrameIndex = 0; FrameIndex < FrameAuthorFrames.Num(); ++FrameIndex)
	{
		const double Distance = FMath::Abs(FrameAuthorFrames[FrameIndex] - Target);
		if (Distance < BestDistance)
		{
			BestDistance = Distance;
			BestFrame = FrameIndex;
		}
	}
	if (BestFrame != INDEX_NONE)
	{
		ScrubPlayback(BestFrame);
	}
}

void FPoseGroupProxyAssetEditor::SetShowKeyframesDuringPlayback(bool bShow)
{
	bShowKeyframesDuringPlayback = bShow;
	if (ViewportWidget.IsValid())
	{
		ViewportWidget->SetPlaybackKeepKeyframes(bShow);
	}
}

void FPoseGroupProxyAssetEditor::SetShowAuthorGhost(bool bShow)
{
	bShowAuthorGhost = bShow;
	if (ViewportWidget.IsValid())
	{
		ViewportWidget->SetShowAuthorGhost(bShow);
	}
}

bool FPoseGroupProxyAssetEditor::IsAnomalyFrame(int32 FrameIndex) const
{
	// 阈值是「肉眼能看出来」的下限，不是质量门禁：
	// 5 cm/帧的位置跳变在 120 fps 下已经是一眼可见的瞬移；
	// 45°/帧 的骨架空间旋转跳变已经是明显的翻面。
	const double PoseStepCm = FrameMaxPoseStepCm.IsValidIndex(FrameIndex)
		? FrameMaxPoseStepCm[FrameIndex] : 0.0;
	const double RotationStepDeg = FrameMaxRotationStepDeg.IsValidIndex(FrameIndex)
		? FrameMaxRotationStepDeg[FrameIndex] : 0.0;
	return PoseStepCm > 5.0 || RotationStepDeg > 45.0;
}

int32 FPoseGroupProxyAssetEditor::GetAnomalyFrameCount() const
{
	int32 Count = 0;
	for (int32 FrameIndex = 0; FrameIndex < GeneratedFrames.Num(); ++FrameIndex)
	{
		if (IsAnomalyFrame(FrameIndex))
		{
			++Count;
		}
	}
	return Count;
}

bool FPoseGroupProxyAssetEditor::IsCurrentPlaybackFrameAnomaly() const
{
	const int32 FrameIndex = GetCurrentPlaybackFrame();
	return GeneratedFrames.IsValidIndex(FrameIndex) && IsAnomalyFrame(FrameIndex);
}

bool FPoseGroupProxyAssetEditor::JumpPlaybackToWorstFrame()
{
	if (GeneratedFrames.Num() == 0)
	{
		return false;
	}

	// 两个指标量纲不同，按各自阈值归一后再比较，避免「角度数大就赢」。
	int32 WorstFrame = INDEX_NONE;
	double WorstScore = 0.0;
	for (int32 FrameIndex = 0; FrameIndex < GeneratedFrames.Num(); ++FrameIndex)
	{
		const double PoseScore = FrameMaxPoseStepCm.IsValidIndex(FrameIndex)
			? FrameMaxPoseStepCm[FrameIndex] / 5.0 : 0.0;
		const double RotationScore = FrameMaxRotationStepDeg.IsValidIndex(FrameIndex)
			? FrameMaxRotationStepDeg[FrameIndex] / 45.0 : 0.0;
		const double Score = FMath::Max(PoseScore, RotationScore);
		if (Score > WorstScore)
		{
			WorstScore = Score;
			WorstFrame = FrameIndex;
		}
	}
	if (WorstFrame == INDEX_NONE || WorstScore < 1.0)
	{
		return false;
	}
	ScrubPlayback(WorstFrame);
	return true;
}

bool FPoseGroupProxyAssetEditor::StepPlaybackToAdjacentAnomaly(int32 Direction)
{
	if (GeneratedFrames.Num() == 0 || Direction == 0)
	{
		return false;
	}
	const int32 Current = GetCurrentPlaybackFrame();
	const int32 Base = Current != INDEX_NONE
		? Current
		: (Direction > 0 ? -1 : GeneratedFrames.Num());

	for (int32 FrameIndex = Base + Direction;
		FrameIndex >= 0 && FrameIndex < GeneratedFrames.Num();
		FrameIndex += Direction)
	{
		if (IsAnomalyFrame(FrameIndex))
		{
			ScrubPlayback(FrameIndex);
			return true;
		}
	}
	return false;
}

float FPoseGroupProxyAssetEditor::GetPlaybackFramesPerSecond() const
{
	// 默认按模型契约帧率播放：生成出来的逐帧数据就是那条时间轴。
	// 只有作者显式勾选「按姿态组帧率播放」时才改用创作帧率——
	// 两者不一致时动作速度会变，这是有意的预览口径，不是误差。
	if (GroupAsset != nullptr
		&& GroupAsset->RefPoseGeneration.bUseGroupFrameRateForPlayback
		&& GroupAsset->Settings.FrameRate > 0.0)
	{
		return static_cast<float>(GroupAsset->Settings.FrameRate);
	}
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

	// 「停止」会退出播放预览，此时不能说成「已暂停」——画面已经回到关键帧视图。
	const bool bPreviewVisible = ViewportWidget.IsValid()
		&& ViewportWidget->IsPlaybackVisible();
	const TCHAR* PlaybackState = !bPreviewVisible
		? TEXT("已停止（关键帧视图）")
		: (bIsPlaying ? TEXT("播放中") : TEXT("已暂停"));
	return FString::Printf(TEXT("%s｜%s｜%s"),
		PlaybackState,
		*FrameText,
		*GenerationStatus);
}

FString FPoseGroupProxyAssetEditor::GetPlaybackDebugText() const
{
	if (GeneratedFrames.Num() == 0)
	{
		return GenerationStatus.IsEmpty()
			? TEXT("尚未生成：点击「生成并播放」后用进度条逐帧检查。")
			: GenerationStatus;
	}
	const int32 FrameIndex = ViewportWidget.IsValid()
		&& ViewportWidget->IsPlaybackVisible()
		? ViewportWidget->GetPlaybackFrameIndex()
		: INDEX_NONE;
	if (!GeneratedFrames.IsValidIndex(FrameIndex))
	{
		return TEXT("已停止（关键帧视图）：拖进度条或点「下一帧」可继续逐帧检查。");
	}

	const int32 SolvedFrame = FrameSolvedFrames.IsValidIndex(FrameIndex)
		? FrameSolvedFrames[FrameIndex]
		: INDEX_NONE;
	const double AuthorFrame = FrameAuthorFrames.IsValidIndex(FrameIndex)
		? FrameAuthorFrames[FrameIndex]
		: 0.0;
	const double GroupFrameRate =
		GroupAsset != nullptr ? GroupAsset->Settings.FrameRate : 0.0;
	const double AuthorSeconds = GroupFrameRate > 0.0
		? AuthorFrame / GroupFrameRate
		: 0.0;
	const int32 WindowIndex = GenerationMetrics.WindowStride > 0
		? FrameIndex / GenerationMetrics.WindowStride
		: 0;
	const bool bHardReference = GenerationMetrics.WindowStride > 0
		&& GenerationMetrics.ReferenceOffsets.Contains(
			FrameIndex % GenerationMetrics.WindowStride);
	const double RotationStep = FrameMaxRotationStepDeg.IsValidIndex(FrameIndex)
		? FrameMaxRotationStepDeg[FrameIndex]
		: 0.0;
	const int32 WorstBoneIndex = FrameWorstRotationBoneIndex.IsValidIndex(FrameIndex)
		? FrameWorstRotationBoneIndex[FrameIndex]
		: INDEX_NONE;
	const FString WorstBoneName = SkeletonBoneNames.IsValidIndex(WorstBoneIndex)
		? SkeletonBoneNames[WorstBoneIndex].ToString()
		: FString(TEXT("—"));
	const double PoseStepCm = FrameMaxPoseStepCm.IsValidIndex(FrameIndex)
		? FrameMaxPoseStepCm[FrameIndex]
		: 0.0;
	const int32 WorstPoseBoneIndex = FrameWorstPoseBoneIndex.IsValidIndex(FrameIndex)
		? FrameWorstPoseBoneIndex[FrameIndex]
		: INDEX_NONE;
	const FString WorstPoseBoneName = SkeletonBoneNames.IsValidIndex(WorstPoseBoneIndex)
		? SkeletonBoneNames[WorstPoseBoneIndex].ToString()
		: FString(TEXT("—"));
	const double PredictionErrorCm =
		FramePredictionErrorCm.IsValidIndex(FrameIndex)
			? FramePredictionErrorCm[FrameIndex]
			: 0.0;

	return FString::Printf(
		TEXT("生成帧 %d/%d ｜ 求解帧 %d ｜ 作者帧 %.1f（%.3f s）｜ 窗口 %d ｜ %s\n")
		TEXT("模型误差 %.1f cm ｜ 旋转跳变 %.1f° @ %s ｜ 位置跳变 %.1f cm @ %s"),
		FrameIndex + 1,
		GeneratedFrames.Num(),
		SolvedFrame,
		AuthorFrame,
		AuthorSeconds,
		WindowIndex + 1,
		bHardReference ? TEXT("硬参考帧") : TEXT("模型生成帧"),
		PredictionErrorCm,
		RotationStep,
		*WorstBoneName,
		PoseStepCm,
		*WorstPoseBoneName);
}

int32 FPoseGroupProxyAssetEditor::GetCurrentPlaybackFrame() const
{
	return ViewportWidget.IsValid() && ViewportWidget->IsPlaybackVisible()
		? ViewportWidget->GetPlaybackFrameIndex()
		: INDEX_NONE;
}

namespace
{
	FString ReferencePatternToString(ERefPoseReferencePattern Pattern)
	{
		switch (Pattern)
		{
		case ERefPoseReferencePattern::None:
			return TEXT("无参考");
		case ERefPoseReferencePattern::Middle:
			return TEXT("中间 (12)");
		case ERefPoseReferencePattern::End:
			return TEXT("末端 (23)");
		case ERefPoseReferencePattern::MiddleAndEnd:
			return TEXT("中间+末端 (12,23)");
		default:
			return TEXT("<未知>");
		}
	}
}

ERefPoseReferencePattern FPoseGroupProxyAssetEditor::GetReferencePattern() const
{
	return GroupAsset != nullptr
		? GroupAsset->RefPoseGeneration.ReferencePattern
		: ERefPoseReferencePattern::MiddleAndEnd;
}

void FPoseGroupProxyAssetEditor::SetReferencePattern(ERefPoseReferencePattern InPattern)
{
	if (GroupAsset == nullptr
		|| GroupAsset->RefPoseGeneration.ReferencePattern == InPattern)
	{
		return;
	}
	const FScopedTransaction Transaction(
		LOCTEXT("ChangeReferencePattern", "修改 RefPose 参考档位"));
	GroupAsset->Modify();
	GroupAsset->RefPoseGeneration.ReferencePattern = InPattern;
	OnGenerationSettingChanged(LOCTEXT("ChangeReferencePatternDone", "修改 RefPose 参考档位"));
}

int32 FPoseGroupProxyAssetEditor::GetStartKeyIndex() const
{
	return GroupAsset != nullptr ? GroupAsset->RefPoseGeneration.StartKeyIndex : 0;
}

void FPoseGroupProxyAssetEditor::SetStartKeyIndex(int32 InKeyIndex)
{
	if (GroupAsset == nullptr)
	{
		return;
	}
	const int32 Clamped = FMath::Clamp(
		InKeyIndex, 0, FMath::Max(0, GroupAsset->Keys.Num() - 1));
	if (GroupAsset->RefPoseGeneration.StartKeyIndex == Clamped)
	{
		return;
	}
	const FScopedTransaction Transaction(
		LOCTEXT("ChangeStartKey", "修改 RefPose 生成起点"));
	GroupAsset->Modify();
	GroupAsset->RefPoseGeneration.StartKeyIndex = Clamped;
	OnGenerationSettingChanged(LOCTEXT("ChangeStartKeyDone", "修改 RefPose 生成起点"));
}

FString FPoseGroupProxyAssetEditor::GetModelPackagePath() const
{
	return GroupAsset != nullptr
		? GroupAsset->RefPoseGeneration.ModelPackagePath
		: FString();
}

void FPoseGroupProxyAssetEditor::SetModelPackagePath(const FString& InPath)
{
	if (GroupAsset == nullptr)
	{
		return;
	}
	FString Trimmed = InPath;
	Trimmed.TrimStartAndEndInline();
	if (GroupAsset->RefPoseGeneration.ModelPackagePath == Trimmed)
	{
		return;
	}
	const FScopedTransaction Transaction(
		LOCTEXT("ChangeModelPackage", "修改 RefPose 模型包路径"));
	GroupAsset->Modify();
	GroupAsset->RefPoseGeneration.ModelPackagePath = Trimmed;
	OnGenerationSettingChanged(LOCTEXT("ChangeModelPackageDone", "修改 RefPose 模型包路径"));
}

bool FPoseGroupProxyAssetEditor::GetUseGroupFrameRateForPlayback() const
{
	return GroupAsset != nullptr
		&& GroupAsset->RefPoseGeneration.bUseGroupFrameRateForPlayback;
}

void FPoseGroupProxyAssetEditor::SetUseGroupFrameRateForPlayback(bool bInUse)
{
	if (GroupAsset == nullptr
		|| GroupAsset->RefPoseGeneration.bUseGroupFrameRateForPlayback == bInUse)
	{
		return;
	}
	const FScopedTransaction Transaction(
		LOCTEXT("ChangePlaybackFrameRate", "修改 RefPose 播放帧率口径"));
	GroupAsset->Modify();
	GroupAsset->RefPoseGeneration.bUseGroupFrameRateForPlayback = bInUse;
	OnGenerationSettingChanged(
		LOCTEXT("ChangePlaybackFrameRateDone", "修改 RefPose 播放帧率口径"));
}

bool FPoseGroupProxyAssetEditor::GetTransferAuthoredTwist() const
{
	return GroupAsset != nullptr
		&& GroupAsset->RefPoseGeneration.bTransferAuthoredTwist;
}

void FPoseGroupProxyAssetEditor::SetTransferAuthoredTwist(bool bInTransfer)
{
	if (GroupAsset == nullptr
		|| GroupAsset->RefPoseGeneration.bTransferAuthoredTwist == bInTransfer)
	{
		return;
	}
	const FScopedTransaction Transaction(
		LOCTEXT("ChangeTwistTransfer", "修改 RefPose 关键帧扭转迁移"));
	GroupAsset->Modify();
	GroupAsset->RefPoseGeneration.bTransferAuthoredTwist = bInTransfer;
	OnGenerationSettingChanged(
		LOCTEXT("ChangeTwistTransferDone", "修改 RefPose 关键帧扭转迁移"));
}

double FPoseGroupProxyAssetEditor::GetStartKeyTimeSeconds() const
{
	if (GroupAsset == nullptr
		|| !GroupAsset->Keys.IsValidIndex(GroupAsset->RefPoseGeneration.StartKeyIndex)
		|| GroupAsset->Settings.FrameRate <= 0.0)
	{
		return 0.0;
	}
	return GroupAsset->Keys[GroupAsset->RefPoseGeneration.StartKeyIndex].Frame
		/ GroupAsset->Settings.FrameRate;
}

void FPoseGroupProxyAssetEditor::OnGenerationSettingChanged(const FText& TransactionText)
{
	if (GroupAsset == nullptr)
	{
		return;
	}
	GroupAsset->MarkPackageDirty();
	MarkGenerationStale();
	UE_LOG(LogMotionWeaver, Display,
		TEXT("[RefPose] 生成设置已修改：%s；参考=%s，起点=%d，包=%s"),
		*TransactionText.ToString(),
		*ReferencePatternToString(GroupAsset->RefPoseGeneration.ReferencePattern),
		GroupAsset->RefPoseGeneration.StartKeyIndex,
		GroupAsset->RefPoseGeneration.ModelPackagePath.IsEmpty()
			? TEXT("<未设置>") : *GroupAsset->RefPoseGeneration.ModelPackagePath);
}

FString FPoseGroupProxyAssetEditor::GetGenerationDiagnosticsText() const
{
	if (GroupAsset == nullptr)
	{
		return TEXT("没有打开的姿态组资产。");
	}

	const FRefPoseGenerationSettings& Settings = GroupAsset->RefPoseGeneration;
	const bool bUseGroupFrameRate =
		Settings.bUseGroupFrameRateForPlayback && GroupAsset->Settings.FrameRate > 0.0;
	const double PlaybackFrameRate = bUseGroupFrameRate
		? GroupAsset->Settings.FrameRate
		: (ModelAsset.IsValid() ? ModelAsset->FrameRate : 120.0);

	FString Text = FString::Printf(
		TEXT("参考档位：%s\n起点：关键帧 %d（帧 %d / %.3f s）\n")
		TEXT("帧率：姿态组 %.3f / 模型 %.4f，播放使用 %s（%.3f fps）\n")
		TEXT("模型包：%s\n"),
		*ReferencePatternToString(Settings.ReferencePattern),
		Settings.StartKeyIndex,
		GroupAsset->Keys.IsValidIndex(Settings.StartKeyIndex)
			? GroupAsset->Keys[Settings.StartKeyIndex].Frame : 0,
		GetStartKeyTimeSeconds(),
		GroupAsset->Settings.FrameRate,
		ModelAsset.IsValid() ? ModelAsset->FrameRate : 0.0,
		bUseGroupFrameRate ? TEXT("姿态组帧率") : TEXT("模型帧率"),
		PlaybackFrameRate,
		Settings.ModelPackagePath.IsEmpty()
			? TEXT("<未设置，使用环境变量或 Saved 覆盖文件>")
			: *Settings.ModelPackagePath);

	if (GeneratedFrames.Num() == 0)
	{
		Text += TEXT("尚未生成：点击「生成并播放」后这里会列出窗口耗时、接缝与锚点误差。\n");
		return Text;
	}
	if (bGenerationStale)
	{
		Text += TEXT("结果已过期：以下数值来自上一次生成，请重新生成后再判断。\n");
	}

	Text += FString::Printf(
		TEXT("生成：%d 窗口 / %d 帧 / 硬参考 %d 帧 / 未映射 %d 骨\n")
		TEXT("起点对应求解样本帧 %d，播放原点帧 %d\n")
		TEXT("推理：总 %.2f ms，峰值 %.2f ms；逐窗口"),
		GenerationMetrics.WindowCount,
		GenerationMetrics.FrameCount,
		GenerationMetrics.HardReferenceFrames,
		UnmappedBoneCount,
		GenerationMetrics.StartSampleFrame,
		GenerationMetrics.PlaybackOriginFrame,
		GenerationMetrics.TotalInferenceMs,
		GenerationMetrics.MaxInferenceMs);
	for (int32 WindowIndex = 0; WindowIndex < GenerationMetrics.WindowInferenceMs.Num(); ++WindowIndex)
	{
		Text += FString::Printf(
			TEXT(" #%d=%.2f ms"), WindowIndex + 1,
			GenerationMetrics.WindowInferenceMs[WindowIndex]);
	}

	int32 RotationSpikeCount = 0;
	int32 PoseSpikeCount = 0;
	for (int32 FrameIndex = 0; FrameIndex < GeneratedFrames.Num(); ++FrameIndex)
	{
		if (FrameMaxRotationStepDeg.IsValidIndex(FrameIndex)
			&& FrameMaxRotationStepDeg[FrameIndex] > 45.0)
		{
			++RotationSpikeCount;
		}
		if (FrameMaxPoseStepCm.IsValidIndex(FrameIndex)
			&& FrameMaxPoseStepCm[FrameIndex] > 5.0)
		{
			++PoseSpikeCount;
		}
	}
	Text += FString::Printf(
		TEXT("\n骨长偏差 %.4f cm，退化骨 %d，Root 接缝最大跳变 %.2f cm，时长膨胀 %.3fx\n")
		TEXT("最大骨架空间旋转跳变 %.2f°/帧 @ %s（>45° 的帧 %d 个）\n")
		TEXT("最大骨架空间位置跳变 %.2f cm/帧 @ %s（>5 cm 的帧 %d 个）\n")
		TEXT("模型位置误差 mean %.2f / max %.2f cm；关键帧扭转迁移 %s（mean %.2f° / max %.2f°）\n"),
		GenerationMetrics.MaxBoneLengthDeviationCm,
		GenerationMetrics.DegenerateBones,
		GenerationMetrics.MaxRootSeamJumpCm,
		GenerationMetrics.TotalTimeDilation,
		GenerationMetrics.MaxGlobalRotationStepDeg,
		*GenerationMetrics.MaxGlobalRotationStepBone,
		RotationSpikeCount,
		GenerationMetrics.MaxPoseStepCm,
		*GenerationMetrics.MaxPoseStepBone,
		PoseSpikeCount,
		GenerationMetrics.MeanPredictionErrorCm,
		GenerationMetrics.MaxPredictionErrorCm,
		GroupAsset->RefPoseGeneration.bTransferAuthoredTwist ? TEXT("开") : TEXT("关"),
		GenerationMetrics.MeanAuthoredTwistDeg,
		GenerationMetrics.MaxAuthoredTwistDeg);

	// 指标口径必须写出来：作者会问「为什么最大值突然小了」。
	Text += FString::Printf(
		TEXT("接缝口径：骨架空间、只统计蒙皮骨（排除 %d 根辅助骨：VB/ik/prop/poi/attach/weapon）\n"),
		GenerationMetrics.SeamMetricExcludedBones.Num());
	if (GenerationMetrics.SeamMetricExcludedBones.Num() > 0)
	{
		Text += TEXT("  排除：");
		for (int32 Index = 0; Index < GenerationMetrics.SeamMetricExcludedBones.Num(); ++Index)
		{
			Text += FString::Printf(
				TEXT("%s%s"), Index > 0 ? TEXT("、") : TEXT(""),
				*GenerationMetrics.SeamMetricExcludedBones[Index]);
		}
		Text += TEXT("\n");
	}

	Text += TEXT("旋转跳变最大的帧（>45°/帧）：\n");
	TArray<int32> SpikeFrames;
	for (int32 FrameIndex = 0; FrameIndex < FrameMaxRotationStepDeg.Num(); ++FrameIndex)
	{
		if (FrameMaxRotationStepDeg[FrameIndex] > 45.0)
		{
			SpikeFrames.Add(FrameIndex);
		}
	}
	SpikeFrames.Sort([this](int32 A, int32 B)
	{
		return FrameMaxRotationStepDeg[A] > FrameMaxRotationStepDeg[B];
	});
	if (SpikeFrames.Num() == 0)
	{
		Text += TEXT("  （无）\n");
	}
	for (int32 Rank = 0; Rank < FMath::Min(5, SpikeFrames.Num()); ++Rank)
	{
		const int32 FrameIndex = SpikeFrames[Rank];
		const int32 WorstBoneIndex = FrameWorstRotationBoneIndex.IsValidIndex(FrameIndex)
			? FrameWorstRotationBoneIndex[FrameIndex]
			: INDEX_NONE;
		const FString BoneName = SkeletonBoneNames.IsValidIndex(WorstBoneIndex)
			? SkeletonBoneNames[WorstBoneIndex].ToString()
			: TEXT("<未知骨>");
		const double AuthorSeconds =
			GroupAsset->Settings.FrameRate > 0.0
				&& FrameAuthorFrames.IsValidIndex(FrameIndex)
			? FrameAuthorFrames[FrameIndex] / GroupAsset->Settings.FrameRate
			: 0.0;
		Text += FString::Printf(
			TEXT("  帧 %d（作者 %.3f s）：%.2f° @ %s\n"),
			FrameIndex + 1, AuthorSeconds,
			FrameMaxRotationStepDeg[FrameIndex], *BoneName);
	}

	Text += TEXT("位置跳变最大的帧（>5 cm/帧）：\n");
	TArray<int32> PoseSpikeFrames;
	for (int32 FrameIndex = 0; FrameIndex < FrameMaxPoseStepCm.Num(); ++FrameIndex)
	{
		if (FrameMaxPoseStepCm[FrameIndex] > 5.0)
		{
			PoseSpikeFrames.Add(FrameIndex);
		}
	}
	PoseSpikeFrames.Sort([this](int32 A, int32 B)
	{
		return FrameMaxPoseStepCm[A] > FrameMaxPoseStepCm[B];
	});
	if (PoseSpikeFrames.Num() == 0)
	{
		Text += TEXT("  （无）\n");
	}
	for (int32 Rank = 0; Rank < FMath::Min(5, PoseSpikeFrames.Num()); ++Rank)
	{
		const int32 FrameIndex = PoseSpikeFrames[Rank];
		const int32 WorstBoneIndex = FrameWorstPoseBoneIndex.IsValidIndex(FrameIndex)
			? FrameWorstPoseBoneIndex[FrameIndex]
			: INDEX_NONE;
		const FString BoneName = SkeletonBoneNames.IsValidIndex(WorstBoneIndex)
			? SkeletonBoneNames[WorstBoneIndex].ToString()
			: TEXT("<未知骨>");
		Text += FString::Printf(
			TEXT("  帧 %d：%.2f cm @ %s\n"),
			FrameIndex + 1, FrameMaxPoseStepCm[FrameIndex], *BoneName);
	}

	Text += TEXT("锚点对齐误差（参考量，不是模型精度）：\n");
	if (AnchorAlignments.Num() == 0)
	{
		Text += TEXT("  （没有可比较的关键帧）\n");
	}
	for (const FRefPoseAnchorAlignment& Alignment : AnchorAlignments)
	{
		Text += FString::Printf(
			TEXT("  关键帧 %d（帧 %d）→ 生成帧 %d：位置 max %.2f / mean %.2f cm，")
			TEXT("旋转 max %.2f / mean %.2f deg（%d 骨）\n"),
			Alignment.KeyIndex, Alignment.KeyFrame, Alignment.GeneratedFrame,
			Alignment.MaxPositionErrorCm, Alignment.MeanPositionErrorCm,
			Alignment.MaxRotationErrorDeg, Alignment.MeanRotationErrorDeg,
			Alignment.ComparedBoneCount);
	}
	for (const FString& Skip : AnchorSkips)
	{
		Text += FString::Printf(TEXT("  跳过：%s\n"), *Skip);
	}

	// 判读提示：这些阈值只用来分流「模型泛化」与「装配/解算出错」，
	// 不参与任何门禁。训练档位的 test RMSE 是 2.38 cm。
	if (GenerationMetrics.MeanPredictionErrorCm > 5.0)
	{
		Text += FString::Printf(
			TEXT("\n判读：模型平均误差 %.2f cm，明显高于训练档位（约 2.4 cm）。\n")
			TEXT("  说明 history 已经落在训练分布外——最常见的关键帧间隔过大、\n")
			TEXT("  中间姿态由插值合成。此时先加密关键帧再比较参数，\n")
			TEXT("  不要用「调低阈值」或「关掉扭转」来让数字变好看。\n"),
			GenerationMetrics.MeanPredictionErrorCm);
	}

	if (GenerationWarnings.Num() > 0)
	{
		Text += FString::Printf(TEXT("告警（%d）：\n"), GenerationWarnings.Num());
		for (const FString& Warning : GenerationWarnings)
		{
			Text += FString::Printf(TEXT("  - %s\n"), *Warning);
		}
	}
	return Text;
}

bool FPoseGroupProxyAssetEditor::ExportGenerationDiagnostics(
	FString& OutPath, FString& OutError)
{
	OutPath.Reset();
	OutError.Reset();
	if (GroupAsset == nullptr)
	{
		OutError = TEXT("没有打开的姿态组资产。");
		return false;
	}
	if (GeneratedFrames.Num() == 0)
	{
		OutError = TEXT("还没有生成结果，无法导出诊断。");
		return false;
	}

	TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
	Root->SetStringField(TEXT("asset"), GroupAsset->GetPathName());
	Root->SetStringField(TEXT("generated_at"), FDateTime::Now().ToIso8601());
	Root->SetBoolField(TEXT("stale"), bGenerationStale);

	TSharedRef<FJsonObject> GroupJson = MakeShared<FJsonObject>();
	GroupJson->SetNumberField(TEXT("key_count"), GroupAsset->Keys.Num());
	GroupJson->SetNumberField(TEXT("frame_rate"), GroupAsset->Settings.FrameRate);
	GroupJson->SetNumberField(TEXT("duration_seconds"), GroupAsset->GetDurationSeconds());
	Root->SetObjectField(TEXT("group"), GroupJson);

	const FRefPoseGenerationSettings& Settings = GroupAsset->RefPoseGeneration;
	TSharedRef<FJsonObject> SettingsJson = MakeShared<FJsonObject>();
	SettingsJson->SetStringField(
		TEXT("reference_pattern"), ReferencePatternToString(Settings.ReferencePattern));
	SettingsJson->SetNumberField(TEXT("start_key_index"), Settings.StartKeyIndex);
	SettingsJson->SetStringField(TEXT("model_package_path"), Settings.ModelPackagePath);
	SettingsJson->SetBoolField(
		TEXT("use_group_frame_rate_for_playback"),
		Settings.bUseGroupFrameRateForPlayback);
	SettingsJson->SetBoolField(
		TEXT("transfer_authored_twist"),
		Settings.bTransferAuthoredTwist);
	SettingsJson->SetNumberField(
		TEXT("playback_frame_rate"), GetPlaybackFramesPerSecond());
	SettingsJson->SetStringField(TEXT("loaded_package_dir"), LoadedPackageDir);
	Root->SetObjectField(TEXT("refpose_settings"), SettingsJson);

	TSharedRef<FJsonObject> MetricsJson = MakeShared<FJsonObject>();
	MetricsJson->SetNumberField(TEXT("window_count"), GenerationMetrics.WindowCount);
	MetricsJson->SetNumberField(TEXT("frame_count"), GenerationMetrics.FrameCount);
	MetricsJson->SetNumberField(
		TEXT("total_inference_ms"), GenerationMetrics.TotalInferenceMs);
	MetricsJson->SetNumberField(
		TEXT("max_inference_ms"), GenerationMetrics.MaxInferenceMs);
	MetricsJson->SetNumberField(
		TEXT("max_bone_length_deviation_cm"),
		GenerationMetrics.MaxBoneLengthDeviationCm);
	MetricsJson->SetNumberField(
		TEXT("degenerate_bones"), GenerationMetrics.DegenerateBones);
	MetricsJson->SetNumberField(
		TEXT("hard_reference_frames"), GenerationMetrics.HardReferenceFrames);
	MetricsJson->SetNumberField(
		TEXT("max_root_seam_jump_cm"), GenerationMetrics.MaxRootSeamJumpCm);
	MetricsJson->SetNumberField(
		TEXT("max_global_rotation_step_deg"),
		GenerationMetrics.MaxGlobalRotationStepDeg);
	MetricsJson->SetStringField(
		TEXT("max_global_rotation_step_bone"),
		GenerationMetrics.MaxGlobalRotationStepBone);
	MetricsJson->SetNumberField(
		TEXT("max_pose_step_cm"), GenerationMetrics.MaxPoseStepCm);
	MetricsJson->SetStringField(
		TEXT("max_pose_step_bone"), GenerationMetrics.MaxPoseStepBone);
	MetricsJson->SetNumberField(
		TEXT("mean_prediction_error_cm"), GenerationMetrics.MeanPredictionErrorCm);
	MetricsJson->SetNumberField(
		TEXT("max_prediction_error_cm"), GenerationMetrics.MaxPredictionErrorCm);
	MetricsJson->SetBoolField(
		TEXT("seam_metrics_deforming_bones_only"), true);
	{
		TArray<TSharedPtr<FJsonValue>> Excluded;
		for (const FString& BoneName : GenerationMetrics.SeamMetricExcludedBones)
		{
			Excluded.Add(MakeShared<FJsonValueString>(BoneName));
		}
		MetricsJson->SetArrayField(TEXT("seam_metric_excluded_bones"), Excluded);
	}
	MetricsJson->SetNumberField(
		TEXT("total_time_dilation"), GenerationMetrics.TotalTimeDilation);
	MetricsJson->SetNumberField(
		TEXT("group_frame_rate"), GenerationMetrics.GroupFrameRate);
	MetricsJson->SetNumberField(
		TEXT("model_frame_rate"), GenerationMetrics.ModelFrameRate);
	MetricsJson->SetNumberField(
		TEXT("start_sample_frame"), GenerationMetrics.StartSampleFrame);
	MetricsJson->SetNumberField(
		TEXT("playback_origin_frame"), GenerationMetrics.PlaybackOriginFrame);
	MetricsJson->SetNumberField(TEXT("unmapped_bone_count"), UnmappedBoneCount);

	TArray<TSharedPtr<FJsonValue>> WindowValues;
	for (const double WindowMs : GenerationMetrics.WindowInferenceMs)
	{
		WindowValues.Add(MakeShared<FJsonValueNumber>(WindowMs));
	}
	MetricsJson->SetArrayField(TEXT("window_inference_ms"), WindowValues);
	Root->SetObjectField(TEXT("metrics"), MetricsJson);

	TArray<TSharedPtr<FJsonValue>> AnchorValues;
	for (const FRefPoseAnchorAlignment& Alignment : AnchorAlignments)
	{
		TSharedRef<FJsonObject> AnchorJson = MakeShared<FJsonObject>();
		AnchorJson->SetNumberField(TEXT("key_index"), Alignment.KeyIndex);
		AnchorJson->SetNumberField(TEXT("key_frame"), Alignment.KeyFrame);
		AnchorJson->SetNumberField(
			TEXT("generated_frame"), Alignment.GeneratedFrame);
		AnchorJson->SetNumberField(
			TEXT("max_position_error_cm"), Alignment.MaxPositionErrorCm);
		AnchorJson->SetNumberField(
			TEXT("mean_position_error_cm"), Alignment.MeanPositionErrorCm);
		AnchorJson->SetNumberField(
			TEXT("max_rotation_error_deg"), Alignment.MaxRotationErrorDeg);
		AnchorJson->SetNumberField(
			TEXT("mean_rotation_error_deg"), Alignment.MeanRotationErrorDeg);
		AnchorJson->SetNumberField(
			TEXT("compared_bone_count"), Alignment.ComparedBoneCount);
		AnchorValues.Add(MakeShared<FJsonValueObject>(AnchorJson));
	}
	Root->SetArrayField(TEXT("anchor_alignment"), AnchorValues);

	TArray<TSharedPtr<FJsonValue>> SkipValues;
	for (const FString& Skip : AnchorSkips)
	{
		SkipValues.Add(MakeShared<FJsonValueString>(Skip));
	}
	Root->SetArrayField(TEXT("anchor_skips"), SkipValues);

	// 逐帧编号：生成帧 → 求解帧 → 作者时间，外加逐帧旋转跳变。
	// 复查「哪一帧开始畸形」时先看这张表，再回到编辑器拖进度条。
	TArray<TSharedPtr<FJsonValue>> SolvedValues;
	TArray<TSharedPtr<FJsonValue>> AuthorValues;
	TArray<TSharedPtr<FJsonValue>> RotationStepValues;
	TArray<TSharedPtr<FJsonValue>> WorstBoneValues;
	TArray<TSharedPtr<FJsonValue>> PoseStepValues;
	TArray<TSharedPtr<FJsonValue>> WorstPoseBoneValues;
	TArray<TSharedPtr<FJsonValue>> PredictionErrorValues;
	for (int32 FrameIndex = 0; FrameIndex < GeneratedFrames.Num(); ++FrameIndex)
	{
		SolvedValues.Add(MakeShared<FJsonValueNumber>(
			FrameSolvedFrames.IsValidIndex(FrameIndex)
				? FrameSolvedFrames[FrameIndex] : INDEX_NONE));
		AuthorValues.Add(MakeShared<FJsonValueNumber>(
			FrameAuthorFrames.IsValidIndex(FrameIndex)
				? FrameAuthorFrames[FrameIndex] : 0.0));
		RotationStepValues.Add(MakeShared<FJsonValueNumber>(
			FrameMaxRotationStepDeg.IsValidIndex(FrameIndex)
				? FrameMaxRotationStepDeg[FrameIndex] : 0.0));
		const int32 WorstBoneIndex =
			FrameWorstRotationBoneIndex.IsValidIndex(FrameIndex)
				? FrameWorstRotationBoneIndex[FrameIndex]
				: INDEX_NONE;
		WorstBoneValues.Add(MakeShared<FJsonValueString>(
			SkeletonBoneNames.IsValidIndex(WorstBoneIndex)
				? SkeletonBoneNames[WorstBoneIndex].ToString()
				: FString()));

		PoseStepValues.Add(MakeShared<FJsonValueNumber>(
			FrameMaxPoseStepCm.IsValidIndex(FrameIndex)
				? FrameMaxPoseStepCm[FrameIndex] : 0.0));
		const int32 WorstPoseBoneIndex =
			FrameWorstPoseBoneIndex.IsValidIndex(FrameIndex)
				? FrameWorstPoseBoneIndex[FrameIndex]
				: INDEX_NONE;
		WorstPoseBoneValues.Add(MakeShared<FJsonValueString>(
			SkeletonBoneNames.IsValidIndex(WorstPoseBoneIndex)
				? SkeletonBoneNames[WorstPoseBoneIndex].ToString()
				: FString()));
		PredictionErrorValues.Add(MakeShared<FJsonValueNumber>(
			FramePredictionErrorCm.IsValidIndex(FrameIndex)
				? FramePredictionErrorCm[FrameIndex] : 0.0));
	}
	Root->SetArrayField(TEXT("frame_solved_frames"), SolvedValues);
	Root->SetArrayField(TEXT("frame_author_frames"), AuthorValues);
	Root->SetArrayField(TEXT("frame_rotation_step_deg"), RotationStepValues);
	Root->SetArrayField(TEXT("frame_worst_rotation_bone"), WorstBoneValues);
	Root->SetArrayField(TEXT("frame_pose_step_cm"), PoseStepValues);
	Root->SetArrayField(TEXT("frame_worst_pose_bone"), WorstPoseBoneValues);
	Root->SetArrayField(TEXT("frame_prediction_error_cm"), PredictionErrorValues);

	TArray<TSharedPtr<FJsonValue>> WarningValues;
	for (const FString& Warning : GenerationWarnings)
	{
		WarningValues.Add(MakeShared<FJsonValueString>(Warning));
	}
	Root->SetArrayField(TEXT("warnings"), WarningValues);
	Root->SetStringField(
		TEXT("note"),
		TEXT("anchor_alignment 是锚点对齐参考量，不是模型精度。"));

	FString Json;
	const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Json);
	if (!FJsonSerializer::Serialize(Root, Writer))
	{
		OutError = TEXT("序列化诊断 JSON 失败。");
		return false;
	}

	const FString Directory =
		FPaths::ProjectSavedDir() / TEXT("MotionWeaver/RefPoseReports");
	if (!IFileManager::Get().MakeDirectory(*Directory, true))
	{
		OutError = FString::Printf(TEXT("无法创建诊断目录：%s"), *Directory);
		return false;
	}
	const FString FileName = FString::Printf(
		TEXT("%s-%s.json"),
		*GroupAsset->GetName(),
		*FDateTime::Now().ToString(TEXT("%Y%m%d-%H%M%S")));
	OutPath = Directory / FileName;
	if (!FFileHelper::SaveStringToFile(Json, *OutPath))
	{
		OutError = FString::Printf(TEXT("写入诊断文件失败：%s"), *OutPath);
		return false;
	}
	UE_LOG(LogMotionWeaver, Display, TEXT("[RefPose] 诊断已导出：%s"), *OutPath);
	return true;
}

#undef LOCTEXT_NAMESPACE
