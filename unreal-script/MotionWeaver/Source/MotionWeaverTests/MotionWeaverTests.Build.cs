// Copyright ZhaoZining. All Rights Reserved.

using UnrealBuildTool;

/**
 * 自动化测试模块。
 *
 * 单独成一个模块的理由：测试不该编进发行包。
 * 之前它们躺在 MotionWeaver/Private/Pose/Proxy/Tests/ 里，
 * 与求解器同模块，打包时一起带走——既增大体积，也把测试代码暴露出去。
 *
 * Type 是 Editor，因为用了 IMPLEMENT_SIMPLE_AUTOMATION_TEST：
 * 那是编辑器侧的自动化框架，游戏运行时不存在。
 */
public class MotionWeaverTests : ModuleRules
{
	public MotionWeaverTests(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

		PrivateDependencyModuleNames.AddRange(new[]
		{
			"Core",
			"CoreUObject",
			"Engine",
			"MotionWeaver",
			// 自动化测试框架在 UnrealEd 里。
			"UnrealEd",
		});
	}
}
