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
			// TypedElementFramework 提供元素类型注册与 handle；
			// TypedElementRuntime 提供选择集。编辑器侧的变换手柄只认这两种类型，
			// 缺一个都会在链接期报未解析符号。
			"TypedElementFramework",
			"TypedElementRuntime",
		});
	}
}
