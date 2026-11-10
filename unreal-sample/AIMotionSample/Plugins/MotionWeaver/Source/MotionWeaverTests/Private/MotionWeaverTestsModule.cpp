// Copyright ZhaoZining. All Rights Reserved.

#include "CoreMinimal.h"
#include "Modules/ModuleManager.h"

/**
 * 测试模块本体。
 *
 * 没有任何启动逻辑：测试用例靠 IMPLEMENT_SIMPLE_AUTOMATION_TEST 静态注册，
 * 模块被加载的那一刻就生效，不需要在这里做额外动作。
 *
 * 但模块实现不能省：少了 IMPLEMENT_MODULE，DLL 能加载却初始化失败，
 * 引擎会报「模块无法被成功初始化」并让整个插件加载失败——
 * 表现是所有测试数变成 0，而编译阶段完全看不出来。
 */
class FMotionWeaverTestsModule : public IModuleInterface
{
public:
	virtual void StartupModule() override {}
	virtual void ShutdownModule() override {}
};

IMPLEMENT_MODULE(FMotionWeaverTestsModule, MotionWeaverTests)
