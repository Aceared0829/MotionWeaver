// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "Modules/ModuleManager.h"

/**
 * MotionWeaver 编辑器模块。
 * 只承担编辑期能力：工具栏入口、从预览提取姿态、资产类型与拖放工厂。
 * 运行时代码一律放在 MotionWeaver 模块，避免打包时带上编辑器依赖。
 */
class FMotionWeaverEditorModule : public IModuleInterface
{
public:
	//~ Begin IModuleInterface Interface
	virtual void StartupModule() override;
	virtual void ShutdownModule() override;
	//~ End IModuleInterface Interface
};
