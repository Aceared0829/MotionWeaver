// Copyright ZhaoZining. All Rights Reserved.

#include "MotionWeaver.h"

#include "Pose/Elements/PoseKeyElementRegistry.h"

void FMotionWeaverModule::StartupModule()
{
	// 把「姿态关键帧」注册成引擎认识的一种 Typed Element 类型。
	//
	// 这一步是编辑器里能用三轴手柄的前提：ITF 的变换手柄只驱动 Typed Element，
	// 我们的编辑单位是资产里的结构体，不注册它，手柄永远不会为它出现。
	FPoseKeyElementRegistry::Register();
}

void FMotionWeaverModule::ShutdownModule()
{
	FPoseKeyElementRegistry::Unregister();
}

IMPLEMENT_MODULE(FMotionWeaverModule, MotionWeaver)
