// Copyright ZhaoZining. All Rights Reserved.

using UnrealBuildTool;

public class MotionWeaver : ModuleRules
{
	public MotionWeaver(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
		IWYUSupport = IWYUSupport.Full;

		PublicDependencyModuleNames.Add("Core");
	}
}
