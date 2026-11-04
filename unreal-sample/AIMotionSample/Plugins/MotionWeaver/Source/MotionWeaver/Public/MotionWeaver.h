// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "Modules/ModuleManager.h"

/**
 * MotionWeaver Runtime 模块的生命周期入口。
 * 当前模块只验证插件加载和编译；具体运行时能力将在职责明确后按模块拆分。
 */
class MOTIONWEAVER_API FMotionWeaverModule : public IModuleInterface
{
public:
	//~ Begin IModuleInterface Interface
	virtual void StartupModule() override;
	virtual void ShutdownModule() override;
	//~ End IModuleInterface Interface
};
