// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Runtime/PoseGroupActor.h"

#include "Components/SceneComponent.h"
#include "Engine/World.h"
#include "Pose/Runtime/PoseComponent.h"
#include "Pose/Data/PoseGroupProxyAsset.h"
#include "Pose/Data/PoseProxyAsset.h"
#include "Pose/Solve/PoseChainDebugComponent.h"

namespace
{
	/** 没有轨迹时的空数组，供 GetSamples 返回引用用。 */
	const TArray<FChainTrajectorySample>& GetEmptySamples()
	{
		static const TArray<FChainTrajectorySample> Empty;
		return Empty;
	}
}

APoseGroupActor::APoseGroupActor()
{
	PrimaryActorTick.bCanEverTick = false;
	PrimaryActorTick.bStartWithTickEnabled = false;

	// Actor 的变换就是整组的原点；资产里的位置都相对它。
	USceneComponent* GroupRoot = CreateDefaultSubobject<USceneComponent>(TEXT("GroupRoot"));
	GroupRoot->SetMobility(EComponentMobility::Movable);
	RootComponent = GroupRoot;

	DebugComponent = CreateDefaultSubobject<UPoseChainDebugComponent>(TEXT("DebugComponent"));
	DebugComponent->SetupAttachment(GroupRoot);

	// 蒙皮组件在 SyncDisplay 里按需增删，这里不预建。
}

const TArray<FChainTrajectorySample>& APoseGroupActor::GetSamples() const
{
	return GroupAsset != nullptr ? GroupAsset->GetSamples() : GetEmptySamples();
}

void APoseGroupActor::OnConstruction(const FTransform& Transform)
{
	Super::OnConstruction(Transform);

	// 构造脚本会改 GroupAsset，重建一次让视口立刻反映新资产。
	SyncDisplay();
}

void APoseGroupActor::PostLoad()
{
	Super::PostLoad();

	// 打包构建下 World 不重跑构造脚本，读档后蒙皮组件必然为空，必须重建。
	SyncDisplay();
}

void APoseGroupActor::RefreshFromAsset()
{
	SyncDisplay();
}

void APoseGroupActor::ClearPoseComponents()
{
	// 先摘再清：直接清数组会让组件变成孤儿，渲染状态与场景代理都不会释放。
	for (TObjectPtr<UPoseComponent>& Component : PoseComponents)
	{
		if (Component != nullptr)
		{
			Component->DestroyComponent();
		}
	}
	PoseComponents.Reset();
}

void APoseGroupActor::SyncDisplay()
{
	USceneComponent* GroupRoot = RootComponent;
	if (GroupRoot == nullptr)
	{
		return;
	}

	// 没有资产或资产为空：清掉残留显示，不要留下一份上一份资产的影子。
	if (GroupAsset == nullptr || GroupAsset->Keys.Num() == 0)
	{
		ClearPoseComponents();
		if (DebugComponent != nullptr)
		{
			DebugComponent->SetVisibility(false);
			DebugComponent->SetChainData(
				GetEmptySamples(), TArray<FPoseKey>(), TArray<FDerivedKey>(),
				TArray<UPoseChainDebugComponent::FStickFigure>(), FChainSettings());
		}
		return;
	}

	UWorld* World = GetWorld();
	const TArray<FPoseKey>& Keys = GroupAsset->Keys;

	// 组件数量对齐到 key 数量。
	while (PoseComponents.Num() > Keys.Num())
	{
		if (UPoseComponent* Extra = PoseComponents.Pop())
		{
			Extra->DestroyComponent();
		}
	}

	// 显示用的火柴人数据与蒙皮一起准备：有真蒙皮的条目不再画骨架线。
	TArray<UPoseChainDebugComponent::FStickFigure> Figures;
	Figures.Reserve(Keys.Num());

	for (int32 Index = 0; Index < Keys.Num(); ++Index)
	{
		const FPoseKey& Key = Keys[Index];
		const bool bHasMesh = Key.PoseAsset != nullptr && !Key.PoseAsset->PreviewMesh.IsNull();

		UPoseChainDebugComponent::FStickFigure Figure;
		if (bHasMesh)
		{
			Figure.Origin = Key.LocalLocation;
			Figure.bHasSkinnedPose = true;
		}
		else
		{
			Figure = UPoseChainDebugComponent::BuildStickFigure(Key.PoseAsset, Key.LocalLocation);
		}
		Figures.Add(MoveTemp(Figure));

		// 没有世界时（构造脚本早期）不创建组件：NewObject 出来的组件没有注册，
		// 写骨骼会静默失败。
		if (World == nullptr || !bHasMesh)
		{
			continue;
		}

		if (!PoseComponents.IsValidIndex(Index) || PoseComponents[Index] == nullptr)
		{
			UPoseComponent* NewComponent =
				NewObject<UPoseComponent>(this, UPoseComponent::StaticClass());
			if (NewComponent == nullptr)
			{
				continue;
			}

			NewComponent->SetupAttachment(GroupRoot);
			NewComponent->RegisterComponent();

			if (PoseComponents.IsValidIndex(Index))
			{
				PoseComponents[Index] = NewComponent;
			}
			else
			{
				PoseComponents.Add(NewComponent);
			}
		}

		UPoseComponent* Component = PoseComponents[Index];
		if (Component == nullptr)
		{
			continue;
		}

		Component->SetRelativeLocationAndRotation(Key.LocalLocation, Key.BodyRotation);
		Component->SetVisibility(bShowGroup);
		Component->ApplyPoseAsset(Key.PoseAsset);
	}

	if (DebugComponent != nullptr)
	{
		DebugComponent->SetVisibility(bShowGroup);
		DebugComponent->SetChainData(
			GroupAsset->GetSamples(), Keys, TArray<FDerivedKey>(),
			Figures, GroupAsset->Settings);
	}
}

#if WITH_EDITOR
void APoseGroupActor::PostEditChangeProperty(FPropertyChangedEvent& PropertyChangedEvent)
{
	Super::PostEditChangeProperty(PropertyChangedEvent);

	// 换资产、改显隐都要立刻反映到视口。
	SyncDisplay();
}
#endif
