// Fill out your copyright notice in the Description page of Project Settings.

using UnrealBuildTool;

public class AIMotionSampleTarget : TargetRules
{
	public AIMotionSampleTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Game;
		DefaultBuildSettings = BuildSettingsVersion.V7;

		ExtraModuleNames.AddRange(["AIMotionSample"]);
	}
}
