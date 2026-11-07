// Copyright ZhaoZining. All Rights Reserved.

#include "MotionWeaverPoseAsset.h"

#if WITH_EDITOR
FString UMotionWeaverPoseAsset::GetDesc()
{
	if (Snapshot.SourceFrame != INDEX_NONE)
	{
		return FString::Printf(TEXT("Pose: %d bones, frame %d"), Snapshot.BoneNames.Num(), Snapshot.SourceFrame);
	}
	return FString::Printf(TEXT("Pose: %d bones"), Snapshot.BoneNames.Num());
}
#endif
