// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "LevelEditorDragDropHandler.h"
#include "PoseChainDragDropHandler.generated.h"

class APoseChainActor;

/**
 * 接管关卡视口的姿势资产拖放。
 *
 * 默认行为是把资产交给 AssetTools 的工厂造一个新 Actor——对姿势资产来说那是
 * APoseActor，于是在链旁边多出一个单帧 Actor，而使用者想的是
 * 「把这个姿势加到链里」。两者都对，但默认走的是前者。
 *
 * 这里只在一种情况下插手：**拖的是姿势资产，且当前恰好选中了一个链 Actor**。
 * 此时改为往 Keys 末尾追加一条（位置取鼠标落点），并取消默认的「新建 Actor」。
 * 其余一切照旧交给引擎，不改变任何其它拖放行为。
 *
 * 为什么用「选中的链 Actor」而不是「鼠标下的 Actor」：
 * 链的根组件没有碰撞体，视口射线打不到它；靠命中测试判定目标不可靠。
 * 选中是确定性的，也和 UE 里「往选中对象里放东西」的习惯一致。
 */
UCLASS(transient, MinimalAPI)
class UPoseChainDragDropHandler : public ULevelEditorDragDropHandler
{
	GENERATED_BODY()

public:
	//~ Begin ULevelEditorDragDropHandler Interface
	virtual bool PreDropObjectsAtCoordinates(
		int32 MouseX, int32 MouseY, UWorld* World, FViewport* Viewport,
		const TArray<UObject*>& DroppedObjects, TArray<AActor*>& OutNewActors) override;
	//~ End ULevelEditorDragDropHandler Interface
};
