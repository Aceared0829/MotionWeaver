// Copyright ZhaoZining. All Rights Reserved.

using UnrealBuildTool;

public class MotionWeaverEditor : ModuleRules
{
	public MotionWeaverEditor(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(new[]
		{
			"Core",
			"CoreUObject",
			"Engine",
			"MotionWeaver",
		});

		PrivateDependencyModuleNames.AddRange(new[]
		{
			"AssetTools",
			"ComponentVisualizers",
			"UnrealEd",
			"Persona",
			"AnimationEditor",
			"LevelEditor",
			"ToolMenus",
			"Slate",
			"SlateCore",
			"AnimGraph",
			"PropertyEditor",
			"AdvancedPreviewScene",
			"EditorFramework",
			"ContentBrowser",
		});
	}
}
