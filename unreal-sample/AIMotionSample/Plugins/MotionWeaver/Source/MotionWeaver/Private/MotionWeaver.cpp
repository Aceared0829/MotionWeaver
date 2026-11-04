// Copyright ZhaoZining. All Rights Reserved.

#include "MotionWeaver.h"

void FMotionWeaverModule::StartupModule()
{
	// 当前插件只验证模块生命周期，不注册功能或资源。
}

void FMotionWeaverModule::ShutdownModule()
{
	// 当前插件没有需要释放的运行时状态。
}

IMPLEMENT_MODULE(FMotionWeaverModule, MotionWeaver)
