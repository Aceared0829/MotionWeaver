// Copyright ZhaoZining. All Rights Reserved.

using UnrealBuildTool;

public class AILocomotionDatasetEditor : ModuleRules
{
	public AILocomotionDatasetEditor(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
		PublicDependencyModuleNames.AddRange(new[] { "Core", "CoreUObject", "Engine", "DeveloperSettings" });
		PrivateDependencyModuleNames.AddRange(new[] { "UnrealEd", "AnimationBlueprintLibrary", "ContentBrowser", "AssetRegistry", "ToolMenus", "Slate", "SlateCore", "Json", "Projects" });
	}
}
