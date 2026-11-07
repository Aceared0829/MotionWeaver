// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "EditorViewportClient.h"

class FAdvancedPreviewScene;

/**
 * 姿势资产的预览视口客户端。
 *
 * 只做"看"这一件事：绕着一个固定的预览场景转、缩放、切换投影，
 * 不接管预览场景里组件的任何状态。
 */
class FMotionWeaverPoseViewportClient final : public FEditorViewportClient
{
public:
	FMotionWeaverPoseViewportClient(FPreviewScene* InPreviewScene, const TSharedRef<SEditorViewport>& InViewport);
};
