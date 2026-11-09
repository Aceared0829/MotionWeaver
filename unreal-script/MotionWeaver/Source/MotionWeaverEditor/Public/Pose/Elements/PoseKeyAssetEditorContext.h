// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "UObject/Object.h"
#include "Tools/AssetEditorContextInterface.h"

#include "PoseKeyAssetEditorContext.generated.h"

class UTypedElementSelectionSet;
class UPoseGroupProxyAsset;

/**
 * 把「当前选中的关键帧」暴露成资产编辑器的选择集。
 *
 * 编辑器变换手柄找目标对象的方式是：从 ToolManager 的 ContextObjectStore 里
 * 取 IAssetEditorContextInterface，再读它返回的选择集
 * （EditorTransformGizmoBuilder.cpp:55-58）。
 * 资产编辑器默认没有这个东西，所以手柄找不到任何可操作对象。
 *
 * 这里只做一件事：维护一个只含「当前帧元素」的选择集。
 */
UCLASS()
class MOTIONWEAVEREDITOR_API UPoseKeyAssetEditorContext
	: public UObject
	, public IAssetEditorContextInterface
{
	GENERATED_BODY()

public:
	/** 换资产并清空选择。 */
	void SetAsset(UPoseGroupProxyAsset* InAsset);

	/** 选中某一帧；INDEX_NONE 表示清空选择。 */
	void SetSelectedKey(int32 InKeyIndex);

	/** 当前选择集里有没有内容。 */
	bool HasSelection() const;

	//~ Begin IAssetEditorContextInterface Interface
	virtual const UTypedElementSelectionSet* GetSelectionSet() const override;
	virtual UTypedElementSelectionSet* GetMutableSelectionSet() override;
	virtual UTypedElementCommonActions* GetCommonActions() override;
	virtual UWorld* GetEditingWorld() const override;
	virtual const IToolkitHost* GetToolkitHost() const override;
	virtual IToolkitHost* GetMutableToolkitHost() override;
	//~ End IAssetEditorContextInterface Interface

private:
	/** 清掉旧的元素句柄，避免 owner 析构断言与泄漏。 */
	void ClearElementHandle();

	UPROPERTY(Transient)
	TObjectPtr<UTypedElementSelectionSet> SelectionSet;

	UPROPERTY(Transient)
	TObjectPtr<UPoseGroupProxyAsset> Asset;

	/** 当前选择集里那个元素的句柄，销毁时要用。 */
	FTypedElementHandle CurrentHandle;

	/** 当前句柄对应的是第几帧。同一帧重复设置时直接返回，避免无谓重建。 */
	int32 CurrentKeyIndex = INDEX_NONE;

	IToolkitHost* ToolkitHost = nullptr;
};
