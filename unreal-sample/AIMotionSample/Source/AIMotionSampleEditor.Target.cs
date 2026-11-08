// Fill out your copyright notice in the Description page of Project Settings.

using UnrealBuildTool;

public class AIMotionSampleEditorTarget : TargetRules
{
	public AIMotionSampleEditorTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Editor;
		DefaultBuildSettings = BuildSettingsVersion.V7;

		ExtraModuleNames.AddRange(["AIMotionSample"]);
	}
}
