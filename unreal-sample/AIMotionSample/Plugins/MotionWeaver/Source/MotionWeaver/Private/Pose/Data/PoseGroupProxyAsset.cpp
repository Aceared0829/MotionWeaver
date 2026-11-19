// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Data/PoseGroupProxyAsset.h"

#include "Core/MotionWeaverLog.h"
#include "Pose/Solve/PoseChain.h"

#include "Misc/DataValidation.h"

UPoseGroupProxyAsset::UPoseGroupProxyAsset()
{
	// 让这个资产可撤销。
	//
	// UDataAsset 的 UCLASS 没有 Transactional 标记，实例默认不带 RF_Transactional；
	// 而 SaveToTransactionBuffer（UObjectGlobals.cpp:3215-3218）要求
	//   GUndo && bIsTransactional && bIsNotScriptPackage
	// 三者同时成立才会把对象写进撤销栈。
	// 缺了 RF_Transactional，Modify() 只标脏不记录，Ctrl+Z 完全没反应。
	//
	// UCLASS 没有对应的 specifier（ObjectMacros.h 里只有运行时 flag），
	// 所以在构造函数里补。
	SetFlags(RF_Transactional);
}

bool UPoseGroupProxyAsset::IsWellFormed() const
{
	if (Keys.Num() == 0 || Settings.FrameRate <= 0.0)
	{
		return false;
	}

	// 帧号必须严格递增且不重复：求解器在重复帧号上直接失败，
	// 资产这边先挡住，报错位置更靠前、更好定位。
	for (int32 Index = 1; Index < Keys.Num(); ++Index)
	{
		if (Keys[Index].Frame <= Keys[Index - 1].Frame)
		{
			return false;
		}
	}

	return true;
}

double UPoseGroupProxyAsset::GetDurationSeconds() const
{
	if (Keys.Num() < 2 || Settings.FrameRate <= 0.0)
	{
		return 0.0;
	}

	// 用首尾帧号而不是「数量 x 间隔」：关键帧之间允许不等距，
	// 跑酷的蓄力段与腾空段节奏本来就不一样。
	const int32 FirstFrame = Keys[0].Frame;
	const int32 LastFrame = Keys.Last().Frame;
	if (LastFrame <= FirstFrame)
	{
		return 0.0;
	}

	return static_cast<double>(LastFrame - FirstFrame) / Settings.FrameRate;
}

void UPoseGroupProxyAsset::Solve()
{
	// Seconds 是派生字段：帧号才是序列化真值。每次求解统一同步一次，
	// 细节面板与时间轴看到的秒数才不会和帧号各说各话。
	for (FPoseKey& Key : Keys)
	{
		Key.Seconds = Settings.FrameRate > 0.0
			? static_cast<double>(Key.Frame) / Settings.FrameRate
			: 0.0;
	}

	// 求解器是纯数据层（只吃数组、只吐数组），不碰 UWorld。
	// 资产直接调它，因此「同一条链」在编辑器、关卡、导出三处算出来的
	// 永远是同一份结果——不存在两套求解实现互相漂移的可能。
	Result.Reset();
	FPoseChainSolver::Solve(Keys, Settings, Result);
}

#if WITH_EDITOR
void UPoseGroupProxyAsset::PostEditChangeProperty(FPropertyChangedEvent& PropertyChangedEvent)
{
	Super::PostEditChangeProperty(PropertyChangedEvent);

	// 不按属性名过滤：改 Keys 里某一项时事件名是 "LocalLocation" 而不是 "Keys"，
	// 白名单会漏掉最常见的那种改动。求解是纯内存计算，一次几毫秒，重算不心疼。
	// 唯一的例外是 Seconds：它是派生字段，编辑后必须先换算回 Frame 再求解，
	// 否则这次改动会被 Solve() 同步覆盖掉，表现为「按秒改了却没生效」。
	if (PropertyChangedEvent.GetPropertyName() == GET_MEMBER_NAME_CHECKED(FPoseKey, Seconds))
	{
		const int32 KeyIndex = PropertyChangedEvent.GetArrayIndex(TEXT("Keys"));
		if (Keys.IsValidIndex(KeyIndex) && Settings.FrameRate > 0.0)
		{
			const int32 OldFrame = Keys[KeyIndex].Frame;
			const double FrameValue = Keys[KeyIndex].Seconds * Settings.FrameRate;

			// 细节面板这条路径没有时间轴那样的错误提示，但也不能把资产改坏：
			// 非法帧号直接拒绝（恢复成旧秒数），并写日志说明原因。
			const bool bValidRange =
				FMath::IsFinite(FrameValue)
				&& FrameValue >= 0.0
				&& FrameValue <= static_cast<double>(TNumericLimits<int32>::Max());
			const int32 NewFrame = bValidRange
				? FMath::Max(0, FMath::RoundToInt(FrameValue))
				: OldFrame;
			const bool bHasPrev = KeyIndex > 0;
			const bool bHasNext = Keys.IsValidIndex(KeyIndex + 1);
			const bool bValidOrder =
				(!bHasPrev || NewFrame > Keys[KeyIndex - 1].Frame)
				&& (!bHasNext || NewFrame < Keys[KeyIndex + 1].Frame);
			if (!bValidRange || !bValidOrder)
			{
				Keys[KeyIndex].Seconds =
					static_cast<double>(OldFrame) / Settings.FrameRate;
				UE_LOG(LogMotionWeaver, Warning,
					TEXT("拒绝关键帧 %d 的秒数修改：%s。"),
					KeyIndex,
					!bValidRange ? TEXT("换算后的帧号超出范围") : TEXT("帧号不满足严格递增"));
			}
			else
			{
				Keys[KeyIndex].Frame = NewFrame;
			}
		}
	}
	Solve();

	// 改完数据要让内容浏览器与已打开的编辑器知道资产变了，
	// 否则资产编辑器视口里画的还是旧轨迹。
	MarkPackageDirty();
}

EDataValidationResult UPoseGroupProxyAsset::IsDataValid(FDataValidationContext& Context) const
{
	EDataValidationResult ValidationResult = Super::IsDataValid(Context);

	if (Keys.Num() == 0)
	{
		Context.AddError(NSLOCTEXT("MotionWeaver", "PoseGroupEmpty",
			"这个姿势组没有任何关键帧。"));
		return EDataValidationResult::Invalid;
	}

	if (Settings.FrameRate <= 0.0)
	{
		Context.AddError(NSLOCTEXT("MotionWeaver", "PoseGroupBadFrameRate",
			"帧率必须为正数。"));
		ValidationResult = EDataValidationResult::Invalid;
	}

	for (int32 Index = 0; Index < Keys.Num(); ++Index)
	{
		if (Keys[Index].PoseAsset == nullptr)
		{
			Context.AddWarning(FText::Format(
				NSLOCTEXT("MotionWeaver", "PoseGroupMissingPose",
					"第 {0} 个关键帧没有姿势资产：它只参与轨迹，摆不出身体。"),
				FText::AsNumber(Index)));
		}

		if (Index > 0 && Keys[Index].Frame <= Keys[Index - 1].Frame)
		{
			Context.AddError(FText::Format(
				NSLOCTEXT("MotionWeaver", "PoseGroupFrameOrder",
					"第 {0} 个关键帧的帧号 {1} 不大于前一个的 {2}；帧号必须严格递增。"),
				FText::AsNumber(Index), FText::AsNumber(Keys[Index].Frame),
				FText::AsNumber(Keys[Index - 1].Frame)));
			ValidationResult = EDataValidationResult::Invalid;
		}
	}

	return ValidationResult;
}
#endif

void UPoseGroupProxyAsset::PostLoad()
{
	Super::PostLoad();

	// 反序列化后 Result 是空的（标了 Transient）：算一次，
	// 这样打开关卡或导出管线直接读 GetSamples() 也能拿到东西。
	if (Keys.Num() > 0)
	{
		Solve();
	}
}
