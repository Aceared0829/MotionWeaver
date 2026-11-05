// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "Framework/MultiBox/MultiBoxExtender.h"

class IAnimationEditor;

/**
 * 动画编辑器工具栏上的「抽取姿势代理」入口。
 *
 * 挂在 AnimationEditor 模块的工具栏扩展点上；该模块自带 IAnimationEditor 引用，
 * 能直接拿到 Persona 工具包，因此不需要另外查找当前编辑器实例。
 */
class FMotionWeaverPoseToolbar
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

	/** 延展器在模块列表中的位置，用于关闭时撤销注册；INDEX_NONE 表示未注册。 */
	static int32 ExtenderIndex;
};
