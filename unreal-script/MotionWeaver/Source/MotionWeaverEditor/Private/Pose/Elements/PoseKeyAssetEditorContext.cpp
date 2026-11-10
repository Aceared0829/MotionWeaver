// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Elements/PoseKeyAssetEditorContext.h"

#include "Pose/Elements/PoseKeyElementRegistry.h"
#include "Pose/Data/PoseGroupProxyAsset.h"
#include "Core/MotionWeaverLog.h"

#include "Editor.h"
#include "Elements/Framework/TypedElementSelectionSet.h"

void UPoseKeyAssetEditorContext::SetAsset(UPoseGroupProxyAsset* InAsset)
{
	if (Asset == InAsset)
	{
		return;
	}

	ClearElementHandle();
	Asset = InAsset;

	const bool bWantSelection = (Asset != nullptr);
	if (bWantSelection && SelectionSet == nullptr)
	{
		SelectionSet = NewObject<UTypedElementSelectionSet>(this);
	}
}

void UPoseKeyAssetEditorContext::ClearElementHandle()
{
	CurrentKeyIndex = INDEX_NONE;

	// 顺序与先后各做一件事，不能混：
	//
	//   1. 先把句柄从选择集里撤掉（选择集会释放它内部那份引用）
	//   2. 再销毁本地句柄（释放我们这份，然后回收元素数据）
	//
	// 反过来的话：元素数据已经被销毁，选择集内部仍指向它，
	// 下一次 SetSelection 或 GetNumSelectedElements 会访问已释放内存，
	// 表现为 EXCEPTION_ACCESS_VIOLATION（崩溃栈落在 TypedElementRuntime）。
	if (CurrentHandle.IsSet())
	{
		if (SelectionSet != nullptr)
		{
			// 用空集合覆盖：等价于「取消选中」，且不会让选择集保留任何句柄引用。
			FTypedElementSelectionOptions Options;
			Options.SetAllowGroups(false);
			SelectionSet->SetSelection(TArray<FTypedElementHandle>(), Options);
		}

		// DestroyHandle 会把 CurrentHandle 清空，调用方不要再重置一次。
		FPoseKeyElementRegistry::DestroyHandle(CurrentHandle);
	}
	else if (SelectionSet != nullptr)
	{
		FTypedElementSelectionOptions Options;
		Options.SetAllowGroups(false);
		SelectionSet->ClearSelection(Options);
	}
}

void UPoseKeyAssetEditorContext::SetSelectedKey(int32 InKeyIndex)
{
	// 同一帧重复设置直接返回。
	//
	// RefreshFromAsset 在每次拖动结束时都会走到这里；
	// 如果每次都销毁再重建句柄，不仅浪费，还会让选择集与句柄
	// 在销毁/重建的间隙里出现引用不一致（曾因此崩在 TypedElementRuntime）。
	const bool bSameKey = (CurrentKeyIndex == InKeyIndex) && CurrentHandle.IsSet();
	if (bSameKey)
	{
		return;
	}

	ClearElementHandle();
	CurrentKeyIndex = INDEX_NONE;

	if (Asset == nullptr || !Asset->Keys.IsValidIndex(InKeyIndex) || SelectionSet == nullptr)
	{
		return;
	}

	FTypedElementHandle Handle =
		FPoseKeyElementRegistry::CreateHandleForKey(Asset, InKeyIndex);

	if (!Handle.IsSet())
	{
		return;
	}

	CurrentHandle = Handle;

	FTypedElementSelectionOptions Options;
	Options.SetAllowGroups(false);

	TArray<FTypedElementHandle> Handles;
	Handles.Add(CurrentHandle);
	SelectionSet->SetSelection(Handles, Options);

	CurrentKeyIndex = InKeyIndex;
}

bool UPoseKeyAssetEditorContext::HasSelection() const
{
	return SelectionSet != nullptr && SelectionSet->GetNumSelectedElements() > 0;
}

const UTypedElementSelectionSet* UPoseKeyAssetEditorContext::GetSelectionSet() const
{
	return SelectionSet;
}

UTypedElementSelectionSet* UPoseKeyAssetEditorContext::GetMutableSelectionSet()
{
	return SelectionSet;
}

UTypedElementCommonActions* UPoseKeyAssetEditorContext::GetCommonActions()
{
	// 不需要复制/粘贴之类的通用动作。
	return nullptr;
}

UWorld* UPoseKeyAssetEditorContext::GetEditingWorld() const
{
	// 与元素接口的 GetOwnerWorld 保持一致：都指向编辑器世界。
	return (GEditor != nullptr) ? GEditor->EditorWorld : nullptr;
}

const IToolkitHost* UPoseKeyAssetEditorContext::GetToolkitHost() const
{
	return ToolkitHost;
}

IToolkitHost* UPoseKeyAssetEditorContext::GetMutableToolkitHost()
{
	return ToolkitHost;
}
