// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/MotionWeaverPoseGroupAsset.h"

#include "Misc/DataValidation.h"

bool UMotionWeaverPoseGroupAsset::IsWellFormed() const
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

double UMotionWeaverPoseGroupAsset::GetDurationSeconds() const
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

#if WITH_EDITOR
EDataValidationResult UMotionWeaverPoseGroupAsset::IsDataValid(FDataValidationContext& Context) const
{
	EDataValidationResult Result = Super::IsDataValid(Context);

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
		Result = EDataValidationResult::Invalid;
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
			Result = EDataValidationResult::Invalid;
		}
	}

	return Result;
}
#endif
