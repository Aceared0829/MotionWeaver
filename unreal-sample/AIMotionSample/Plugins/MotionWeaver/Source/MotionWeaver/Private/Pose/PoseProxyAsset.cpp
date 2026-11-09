// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/PoseProxyAsset.h"

#if WITH_EDITOR
FString UPoseProxyAsset::GetDesc()
{
	if (Snapshot.SourceFrame != INDEX_NONE)
	{
		return FString::Printf(TEXT("Pose: %d bones, frame %d"), Snapshot.BoneNames.Num(), Snapshot.SourceFrame);
	}
	return FString::Printf(TEXT("Pose: %d bones"), Snapshot.BoneNames.Num());
}
#endif
