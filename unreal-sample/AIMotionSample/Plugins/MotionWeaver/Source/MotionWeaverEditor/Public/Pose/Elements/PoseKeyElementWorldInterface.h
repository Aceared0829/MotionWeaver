// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "UObject/Object.h"
#include "Elements/Interfaces/TypedElementWorldInterface.h"

#include "PoseKeyElementWorldInterface.generated.h"

/**
 * 让 PoseKey 元素成为一个「世界里的可变换对象」。
 *
 * 编辑器变换手柄（UTransformGizmo）只驱动实现了这个接口的元素，
 * 而且还会在 EditorElementSubsystem.cpp:83-89 里做一次世界归属检查：
 *
 *     if (OwnerWorld != InRequiredWorld) { return false; }  // 被剔除
 *
 * 而 gizmo 传进来的 InRequiredWorld 恒为 GEditor->EditorWorld。
 * 我们的关键帧数据不在任何世界里，所以 GetOwnerWorld 必须自己声明
 * 「我在 EditorWorld」，否则元素会在到达 gizmo 之前就被过滤掉。
 * 这是整套方案能成立的关键一行，别改成返回预览场景的世界。
 */
UCLASS()
class MOTIONWEAVEREDITOR_API UPoseKeyElementWorldInterface
	: public UObject
	, public ITypedElementWorldInterface
{
	GENERATED_BODY()

public:
	//~ Begin ITypedElementWorldInterface Interface
	virtual bool IsTemplateElement(const FTypedElementHandle& InElementHandle) override;
	virtual bool CanEditElement(const FTypedElementHandle& InElementHandle) override;
	virtual UWorld* GetOwnerWorld(const FTypedElementHandle& InElementHandle) override;
	virtual ULevel* GetOwnerLevel(const FTypedElementHandle& InElementHandle) override;
	virtual bool GetBounds(const FTypedElementHandle& InElementHandle, FBoxSphereBounds& OutBounds) override;
	virtual bool CanMoveElement(const FTypedElementHandle& InElementHandle,
		const ETypedElementWorldType InWorldType) override;
	virtual bool CanScaleElement(const FTypedElementHandle& InElementHandle) override;
	virtual bool GetWorldTransform(const FTypedElementHandle& InElementHandle,
		FTransform& OutTransform) override;
	virtual bool SetWorldTransform(const FTypedElementHandle& InElementHandle,
		const FTransform& InTransform) override;
	virtual bool GetRelativeTransform(const FTypedElementHandle& InElementHandle,
		FTransform& OutTransform) override;
	virtual bool SetRelativeTransform(const FTypedElementHandle& InElementHandle,
		const FTransform& InTransform) override;
	virtual void NotifyMovementStarted(const FTypedElementHandle& InElementHandle) override;
	virtual void NotifyMovementOngoing(const FTypedElementHandle& InElementHandle) override;
	virtual void NotifyMovementEnded(const FTypedElementHandle& InElementHandle) override;
	//~ End ITypedElementWorldInterface Interface

	/**
	 * 拖拽开始/进行中/结束的广播。
	 * 编辑器接到后进行开合事务、同步预览组件、松手求解。
	 * 用静态委托是因为接口实例由 Registry 持有，编辑器拿不到它。
	 */
	DECLARE_MULTICAST_DELEGATE(FOnMovement);
	static FOnMovement& OnMovementStarted();
	static FOnMovement& OnMovementOngoing();
	static FOnMovement& OnMovementEnded();
};
