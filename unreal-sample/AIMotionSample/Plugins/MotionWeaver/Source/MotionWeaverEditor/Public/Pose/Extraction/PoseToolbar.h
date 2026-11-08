// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "Framework/MultiBox/MultiBoxExtender.h"
#include "IAnimationEditorModule.h"

class IAnimationEditor;

/**
 * 动画编辑器工具栏上的「抽取姿势代理」入口。
 *
 * 挂在 AnimationEditor 模块的工具栏扩展点上；该模块自带 IAnimationEditor 引用，
 * 能直接拿到 Persona 工具包，因此不需要另外查找当前编辑器实例。
 */
class FPoseToolbar
{
public:
	/** 注册到 AnimationEditor 模块；重复调用安全。 */
	static void Register();

	/** 撤销注册，模块关闭时调用。 */
	static void Unregister();

private:
	/** 构造延展器：在工具栏末尾追加按钮。 */
	static TSharedRef<FExtender> ExtendToolbar(const TSharedRef<FUICommandList> CommandList, TSharedRef<IAnimationEditor> AnimationEditor);

	/** 按钮点击：从当前暂停帧抽出姿势代理资产。按值接收，与 TDelegate::CreateStatic 的绑定方式一致。 */
	static void OnExtractClicked(TWeakPtr<IAnimationEditor> WeakAnimationEditor);

	/** 是否允许点击：正在播放时禁用。同样按值接收。 */
	static bool CanExtractClicked(TWeakPtr<IAnimationEditor> WeakAnimationEditor);

	/**
	 * 已注册的延展器副本。
	 *
	 * 不用下标登记：模块返回的是普通 TArray<委托>，没有句柄语义。
	 * 别人在我们的槽位之前增删条目、或模块热重载重建数组，下标都会失效并删错别人的项。
	 * 因此保留一份副本，退订时按它的 GetHandle() 定位（TDelegate 没有 operator==）。
	 */
	static IAnimationEditorModule::FAnimationEditorToolbarExtender RegisteredExtender;
};
