// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Elements/PoseKeyElementWorldInterface.h"

#include "Pose/Elements/PoseKeyElementData.h"
#include "Pose/PoseGroupProxyAsset.h"
#include "Core/MotionWeaverLog.h"

#include "Editor.h"
#include "EditorSupportDelegates.h"
#include "ScopedTransaction.h"
#include "UnrealEdGlobals.h"
#include "Engine/World.h"

namespace
{
	/**
	 * 从元素句柄里取出「是哪个资产的哪一帧」。
	 * 取不到（资产已回收 / 索引越界）时返回 nullptr。
	 */
	const FPoseKeyElementData* GetPoseKeyData(const FTypedElementHandle& InElementHandle)
	{
		// 整条路径都用 Private_GetInternalData，绕开 GetId() / IsSet<T>()。
		//
		// 原因：TypedElementHandle.h:106-111 的 GetId() 在句柄为空时
		// 返回 FTypedElementId::Unset；那个常量在本工程（Installed Build）
		// 里没有正确导出，凡是内联展开到别处就会变成未解析的 dllimport 符号。
		// IsSet() 只判指针、本身安全，但 IsDataOfType() 内部又走了 GetId()，
		// 所以类型判断也一并用 StaticTypeId 自己做。
		const FTypedElementInternalData* Internal = InElementHandle.Private_GetInternalData();
		if (Internal == nullptr)
		{
			return nullptr;
		}

		if (Internal->GetId().GetTypeId() != FPoseKeyElementData::StaticTypeId())
		{
			return nullptr;
		}

		const TTypedElementInternalData<FPoseKeyElementData>* TypedInternal =
			static_cast<const TTypedElementInternalData<FPoseKeyElementData>*>(Internal);
		return &TypedInternal->GetData();
	}

	FPoseKey* ResolvePoseKey(const FTypedElementHandle& InElementHandle)
	{
		const FPoseKeyElementData* Data = GetPoseKeyData(InElementHandle);
		if (Data == nullptr || !Data->IsValid())
		{
			return nullptr;
		}

		UPoseGroupProxyAsset* Asset = Data->Asset.Get();
		if (Asset == nullptr || !Asset->Keys.IsValidIndex(Data->KeyIndex))
		{
			return nullptr;
		}

		return &Asset->Keys[Data->KeyIndex];
	}
}

bool UPoseKeyElementWorldInterface::IsTemplateElement(const FTypedElementHandle&)
{
	return false;
}

bool UPoseKeyElementWorldInterface::CanEditElement(const FTypedElementHandle& InElementHandle)
{
	return ResolvePoseKey(InElementHandle) != nullptr;
}

UWorld* UPoseKeyElementWorldInterface::GetOwnerWorld(const FTypedElementHandle&)
{
	// 必须是 EditorWorld。
	//
	// EditorElementSubsystem.cpp:83-89 会拿这里返回的世界与 gizmo 传入的
	// GEditor->EditorWorld 做严格相等比较，不等就把元素从选择集里剔除。
	// 关键帧数据本身不属于任何世界，这个返回值纯粹是「声明归属」，
	// 用来通过那道检查。改成预览场景的世界会立刻被过滤掉。
	return (GEditor != nullptr) ? GEditor->EditorWorld : nullptr;
}

ULevel* UPoseKeyElementWorldInterface::GetOwnerLevel(const FTypedElementHandle&)
{
	// 不依赖具体关卡：返回空即可，引擎不会因此拒绝操纵。
	return nullptr;
}

bool UPoseKeyElementWorldInterface::GetBounds(
	const FTypedElementHandle& InElementHandle, FBoxSphereBounds& OutBounds)
{
	const FPoseKey* Key = ResolvePoseKey(InElementHandle);
	if (Key == nullptr)
	{
		return false;
	}

	// 给一个半径 50cm 的包围球：够框选命中，也够相机聚焦时有个合理体积。
	OutBounds = FBoxSphereBounds(Key->LocalLocation, FVector(50.0), 50.0);
	return true;
}

bool UPoseKeyElementWorldInterface::CanMoveElement(
	const FTypedElementHandle&, const ETypedElementWorldType)
{
	// 返回 false 会在 EditorElementSubsystem.cpp:73 被剔除，
	// 手柄就永远不出现。这里恒真。
	return true;
}

bool UPoseKeyElementWorldInterface::CanScaleElement(const FTypedElementHandle&)
{
	// 不开放缩放：姿态的求解只吃位置与偏航，缩放没有语义。
	return false;
}

bool UPoseKeyElementWorldInterface::GetWorldTransform(
	const FTypedElementHandle& InElementHandle, FTransform& OutTransform)
{
	const FPoseKey* Key = ResolvePoseKey(InElementHandle);
	if (Key == nullptr)
	{
		return false;
	}

	// FPoseKey 里的位置与朝向本来就是「相对起点」的世界量，
	// 直接给出去即可，不需要再叠加任何父级变换。
	OutTransform = FTransform(Key->BodyRotation, Key->LocalLocation);
	return true;
}

bool UPoseKeyElementWorldInterface::SetWorldTransform(
	const FTypedElementHandle& InElementHandle, const FTransform& InTransform)
{
	const FPoseKeyElementData* Data = GetPoseKeyData(InElementHandle);
	if (Data == nullptr || !Data->IsValid())
	{
		return false;
	}

	UPoseGroupProxyAsset* Asset = Data->Asset.Get();
	FPoseKey* Key = ResolvePoseKey(InElementHandle);
	if (Asset == nullptr || Key == nullptr)
	{
		return false;
	}

	// 探针：确认事务是否真的开着、对象是否可撤销。
	// 这两条是 Ctrl+Z 能否生效的硬前提。
	UE_LOG(LogMotionWeaver, Warning,
		TEXT("[PoseKeyElement] SetWorldTransform: GUndo=%s Transactional=%s"),
		(GUndo != nullptr) ? TEXT("有") : TEXT("空"),
		Asset->HasAnyFlags(RF_Transactional) ? TEXT("是") : TEXT("否"));

	// 标脏交给资产：撤销与保存都依赖它。
	Asset->Modify();

	Key->LocalLocation = InTransform.GetLocation();

	// 旋转只保留偏航。
	// 这个系统的朝向语义就是 Yaw（模型契约里 Root 轨迹只有位置与偏航），
	// Pitch/Roll 写回去会让角色侧翻，之前踩过这个坑。
	FRotator NewRotation = InTransform.Rotator();
	NewRotation.Pitch = 0.0;
	NewRotation.Roll = 0.0;
	Key->BodyRotation = NewRotation;
	Key->HeadingDegrees = NewRotation.Yaw;

	Asset->MarkPackageDirty();

	// 通知细节面板刷新。
	// 少了这一句，拖完之后面板里的数字还停在旧值，
	// 看起来像「拖了但没生效」——这是最容易让人误判的一条。
	FEditorSupportDelegates::RefreshPropertyWindows.Broadcast();
	FEditorSupportDelegates::UpdateUI.Broadcast();

	return true;
}

UPoseKeyElementWorldInterface::FOnMovement& UPoseKeyElementWorldInterface::OnMovementStarted()
{
	static FOnMovement Delegate;
	return Delegate;
}

UPoseKeyElementWorldInterface::FOnMovement& UPoseKeyElementWorldInterface::OnMovementOngoing()
{
	static FOnMovement Delegate;
	return Delegate;
}

UPoseKeyElementWorldInterface::FOnMovement& UPoseKeyElementWorldInterface::OnMovementEnded()
{
	static FOnMovement Delegate;
	return Delegate;
}

void UPoseKeyElementWorldInterface::NotifyMovementStarted(const FTypedElementHandle&)
{
	UE_LOG(LogMotionWeaver, Log, TEXT("[PoseKeyElement] 开始拖动"));
	OnMovementStarted().Broadcast();
}

void UPoseKeyElementWorldInterface::NotifyMovementOngoing(const FTypedElementHandle&)
{
	OnMovementOngoing().Broadcast();
}

void UPoseKeyElementWorldInterface::NotifyMovementEnded(const FTypedElementHandle&)
{
	UE_LOG(LogMotionWeaver, Log, TEXT("[PoseKeyElement] 结束拖动"));
	OnMovementEnded().Broadcast();
}

bool UPoseKeyElementWorldInterface::GetRelativeTransform(
	const FTypedElementHandle& InElementHandle, FTransform& OutTransform)
{
	// 关键帧没有父级，相对变换与世界变换是同一件事。
	return GetWorldTransform(InElementHandle, OutTransform);
}

bool UPoseKeyElementWorldInterface::SetRelativeTransform(
	const FTypedElementHandle& InElementHandle, const FTransform& InTransform)
{
	return SetWorldTransform(InElementHandle, InTransform);
}
