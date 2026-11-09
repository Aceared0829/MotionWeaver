// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "EditorViewportClient.h"
#include "Pose/Proxy/PoseChainTypes.h"

class UPoseGroupProxyAsset;
class FAdvancedPreviewScene;

/**
 * 姿态代理组编辑器的视口客户端。
 *
 * 与单帧姿态的 FPoseViewportClient 是两个独立的类（那个是 final，不能继承）：
 * 那个只看一帧，这个要在同一份预览场景里画整段运动——当前帧的姿态网格、
 * 其余帧的骨架幽灵、以及求解出来的地面轨迹与方向箭头。
 *
 * 轨迹与幽灵都直接在 Draw() 里画（PDI），不用 DebugDrawComponent：
 * 那个组件是给关卡 Actor 用的，硬塞进预览场景要额外接管场景代理与
 * 生命周期，而编辑器视口的画线本来就是一次 Draw 调用的事。
 */
class FPoseGroupViewportClient final : public FEditorViewportClient
{
public:
	FPoseGroupViewportClient(FPreviewScene* InPreviewScene, const TSharedRef<SEditorViewport>& InViewport);

	/** 设置要显示的资产。为空时只画空场景。 */
	void SetGroupAsset(UPoseGroupProxyAsset* InAsset);

	/** 当前正在看第几个关键帧。INDEX_NONE 表示没有选中。 */
	void SetSelectedKey(int32 InKeyIndex);

	int32 GetSelectedKey() const { return SelectedKeyIndex; }

	//~ Begin FEditorViewportClient Interface
	virtual void Draw(const FSceneView* View, FPrimitiveDrawInterface* PDI) override;
	//~ End FEditorViewportClient Interface

private:
	/** 画求解出来的地面轨迹 + 每个关键帧的朝向箭头。 */
	void DrawTrajectory(FPrimitiveDrawInterface* PDI) const;

	/** 画除当前帧以外的关键帧骨架（幽灵），帮作者看清前后关系。 */
	void DrawGhostKeys(FPrimitiveDrawInterface* PDI) const;

	TWeakObjectPtr<UPoseGroupProxyAsset> GroupAsset;

	int32 SelectedKeyIndex = INDEX_NONE;
};
