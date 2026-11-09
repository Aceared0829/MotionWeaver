// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "Elements/Framework/TypedElementHandle.h"

class UPoseGroupProxyAsset;
struct FPoseKeyElementData;

/**
 * 把「姿态关键帧」注册成引擎认识的一种 Typed Element 类型。
 *
 * 为什么要注册一种新类型：
 * ITF 的变换手柄只认 Typed Element。我们的编辑单位是一个资产里的结构体，
 * 既不是 Actor 也不是 Component，引擎不认识它，手柄自然不会为它出现。
 * 注册之后它就能进入选择集，被 UEditorTransformProxy 找到并驱动。
 *
 * 只在运行时模块注册，且只做一次；重复注册会被引擎断言拦下。
 */
class MOTIONWEAVER_API FPoseKeyElementRegistry
{
public:
	/** 元素类型名。注册与取用必须用同一个。 */
	static const FName ElementTypeName;

	/**
	 * 注册元素类型与它支持的接口。
	 * 必须在 Typed Element Registry 已经初始化之后调用（模块启动期即可）。
	 */
	static void Register();

	/** 注销。模块关闭时调用。 */
	static void Unregister();

	/** 是否已经注册成功。 */
	static bool IsRegistered();

	/**
	 * 把世界变换接口挂到已注册的元素类型上。
	 * 单独一个入口是因为该接口的实现依赖 GEditor，必须放在编辑器模块里；
	 * 由编辑器模块在启动时调用，调用前须先 Register()。
	 */
	static void RegisterWorldInterface(UObject* WorldInterfaceInstance);

	/**
	 * 为一个关键帧创建元素，返回可放进选择集的 handle。
	 * 失败时返回无效 handle。
	 */
	static FTypedElementHandle CreateHandleForKey(UPoseGroupProxyAsset* Asset, int32 KeyIndex);

	/**
	 * 销毁一个由 CreateHandleForKey 创建的元素。
	 *
	 * 参数是引用且会被清空：句柄自身持有一份引用计数，
	 * 必须先把它释放掉，再去销毁元素本体；顺序反了会访问已销毁的元素数据。
	 * 调用方不必、也不应该再自己重置这个句柄。
	 */
	static void DestroyHandle(FTypedElementHandle& Handle);
};