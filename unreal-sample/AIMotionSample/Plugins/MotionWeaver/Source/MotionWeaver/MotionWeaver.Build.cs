// Copyright ZhaoZining. All Rights Reserved.

using UnrealBuildTool;

public class MotionWeaver : ModuleRules
{
	public MotionWeaver(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
		IWYUSupport = IWYUSupport.Full;

		PublicDependencyModuleNames.AddRange(new[]
		{
			"Core",
			"CoreUObject",
			"Engine",
			"NNE",
			// 模型契约（skeleton.json）解析。Json 只在 Runtime 用，但模块内的
			// 私有实现也要链接到它，因此放 Public 以免下游重复声明。
			"Json",
			// TypedElementFramework 提供元素类型注册与 handle；
			// TypedElementRuntime 提供选择集。编辑器侧的变换手柄只认这两种类型，
			// 缺一个都会在链接期报未解析符号。
			"TypedElementFramework",
			"TypedElementRuntime",
		});
	}
}
