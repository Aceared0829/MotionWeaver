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
			// EKeys::* 定义在 InputCore；SListView / STableRow 展开后要用到它，
			// 少了这个模块会在链接期报一堆 EKeys 未解析。
			"InputCore",
		});
	}
}
