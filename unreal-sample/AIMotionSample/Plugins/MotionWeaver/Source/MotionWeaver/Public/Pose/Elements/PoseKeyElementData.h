// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "Elements/Framework/TypedElementData.h"

class UPoseGroupProxyAsset;

/**
 * 姿态关键帧在 Typed Element 系统里的元素数据。
 *
 * 为什么不把 FPoseKey 本身改成 UObject：
 * 引擎自己的元素数据也不是 UObject，而是「弱引用 + 索引」这种轻量结构。
 * 见 ComponentElementData.h：它放的是 TWeakObjectPtr<UActorComponent>。
 * 元素只是「指向资产里某个 Key」的一张借据，真值仍然在资产上。
 *
 * 弱引用是必须的：元素 handle 的销毁是延迟的，
 * 可能晚于资产本身被回收，强指针会在那一段窗口里变成悬空访问。
 */
struct FPoseKeyElementData
{
	// 第二个参数是导出宏：这个 RTTI 的静态成员定义在运行时模块的 cpp 里，
	// 编辑器模块也要读它，所以必须导出，否则链接期会报未解析符号。
	UE_DECLARE_TYPED_ELEMENT_DATA_RTTI(FPoseKeyElementData, MOTIONWEAVER_API);

	/** 这个元素指向哪个资产。 */
	TWeakObjectPtr<UPoseGroupProxyAsset> Asset;

	/** 指向资产 Keys 数组里的第几项。 */
	int32 KeyIndex = INDEX_NONE;

	/** 数据是否还能用：资产还活着且索引有效。 */
	bool IsValid() const
	{
		return Asset.IsValid() && KeyIndex != INDEX_NONE;
	}
};
