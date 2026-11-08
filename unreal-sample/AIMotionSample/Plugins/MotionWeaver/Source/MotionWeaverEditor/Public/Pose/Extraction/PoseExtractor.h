// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"

class IPersonaToolkit;
class UPoseProxyAsset;

/**
 * 从动画预览中提取当前暂停帧的姿态。
 *
 * 调用方负责提供 IPersonaToolkit（工具栏回调里本来就有 IAnimationEditor，
 * 直接就能拿到），这里不自己去猜"当前打开的是哪个编辑器"——UE 关了 RTTI，
 * 从 IAssetEditorInstance 反查接口既不可靠也不必要。
 */
class FPoseExtractor
{
public:
	/**
	 * 检查该工具包的预览是否处于可提取状态。
	 * @param Toolkit 目标 Persona 工具包。
	 * @param OutReason 不可提取时给出原因，可直接显示给用户。
	 * @return 可以提取返回 true。
	 */
	static bool CanExtract(IPersonaToolkit& Toolkit, FText& OutReason);

	/**
	 * 把预览的当前帧写进一份新的姿势资产。
	 *
	 * @param Toolkit          目标 Persona 工具包。
	 * @param OuterPackagePath 新资产所在的内容目录，例如 /Game/Poses。
	 * @param OutError         失败原因。
	 * @return 创建出的资产；失败时返回 nullptr。
	 */
	static UPoseProxyAsset* ExtractToNewAsset(IPersonaToolkit& Toolkit, const FString& OuterPackagePath, FText& OutError);
};
