// Fill out your copyright notice in the Description page of Project Settings.

using UnrealBuildTool;

public class AIMotionSampleTarget : TargetRules
{
	public AIMotionSampleTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Game;
		DefaultBuildSettings = BuildSettingsVersion.V8;
		IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_9;

		ExtraModuleNames.AddRange(["AIMotionSample"]);
	}
}
