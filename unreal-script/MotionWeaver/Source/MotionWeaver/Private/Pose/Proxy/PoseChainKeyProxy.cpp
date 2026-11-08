// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Proxy/PoseChainKeyProxy.h"

#include "Components/SceneComponent.h"
#include "Pose/PoseProxyAsset.h"
#include "Pose/PoseComponent.h"
#include "Pose/Proxy/PoseChainActor.h"

APoseChainKeyProxy::APoseChainKeyProxy()
{
	PrimaryActorTick.bCanEverTick = false;
	PrimaryActorTick.bStartWithTickEnabled = false;

	USceneComponent* ProxyRoot = CreateDefaultSubobject<USceneComponent>(TEXT("ProxyRoot"));
	ProxyRoot->SetMobility(EComponentMobility::Movable);
	RootComponent = ProxyRoot;

	PoseDisplay = CreateDefaultSubobject<UPoseComponent>(TEXT("PoseDisplay"));
	PoseDisplay->SetupAttachment(ProxyRoot);

	// 代理是编辑手柄：不进游戏视图，不参与碰撞与伤害，也不烘焙进关卡。
	bIsEditorOnlyActor = true;
	SetActorHiddenInGame(true);
	SetCanBeDamaged(false);

	// 场景大纲里不列出来——用户关心的是那条链，不是它派生出来的一堆代理。
	// 隐藏不等于不可点：视口命中走图元，不走大纲。
	bListedInSceneOutliner = false;
}

void APoseChainKeyProxy::SyncFromChain(
	const FVector& LocalLocation,
	const FRotator& BodyRotation,
	UPoseProxyAsset* PoseAsset,
	bool bVisible,
	bool bForcePoseRefresh)
{
	// 同步期间的位置变化不是用户输入，PostEditMove 必须让路，
	// 否则「链写进来 -> PostEditMove 读出去写回链」会形成自激。
	TGuardValue<bool> SyncGuard(bIsSyncingFromChain, true);

	SetActorRelativeLocation(LocalLocation, false, nullptr, ETeleportType::TeleportPhysics);
	// 完整朝向：Pitch/Roll 不参与求解，但代理必须转过去，
	// 否则作者在视口里转出来的姿态会在下一次同步时被抹平。
	SetActorRelativeRotation(BodyRotation, false, nullptr, ETeleportType::TeleportPhysics);

	LastSyncedWorldLocation = GetActorLocation();
	LastSyncedWorldRotation = GetActorRotation();

	if (PoseDisplay == nullptr)
	{
		return;
	}

	PoseDisplay->SetVisibility(bVisible);

	// 只在真显示、且资产确实需要重灌时才碰骨骼：
	// 拖一次 key 会触发全链重建，逐个重灌蒙皮会把拖动拖成卡顿。
	if (bVisible && bForcePoseRefresh)
	{
		PoseDisplay->ApplyPoseAsset(PoseAsset);
	}
}

void APoseChainKeyProxy::PostEditMove(bool bFinished)
{
	Super::PostEditMove(bFinished);

	// 链在同步时改的位置不是用户输入，不能回写。
	if (bIsSyncingFromChain)
	{
		return;
	}

	APoseChainActor* Chain = OwningChain.Get();
	if (Chain == nullptr || SortedIndex == INDEX_NONE)
	{
		return;
	}

	const FVector CurrentWorldLocation = GetActorLocation();

	// 位置和朝向都没变就不要打扰链。拖动整条链时每个代理都会收到
	// PostEditMove，无差别回写会把一次拖动变成 N 次全链重算。
	const bool bMoved = !CurrentWorldLocation.Equals(LastSyncedWorldLocation, 0.01);
	const bool bRotated = !GetActorRotation().Equals(LastSyncedWorldRotation, 0.05);
	if (!bMoved && !bRotated)
	{
		return;
	}

	LastSyncedWorldLocation = CurrentWorldLocation;
	LastSyncedWorldRotation = GetActorRotation();

	// 世界 → 链局部。key 的位置本来就相对链 Actor，
	// 因此整条链被整体移动时，单点拖动依然只影响那一个点。
	const FTransform& ChainTransform = Chain->GetActorTransform();
	const FVector LocalLocation = ChainTransform.InverseTransformPosition(CurrentWorldLocation);

	// 完整旋转都要留下来：Yaw 进求解器决定 Root 路径，Pitch/Roll 进 BodyRotation 决定姿态朝向。
	const FRotator LocalRotation =
		(ChainTransform.GetRotation().Inverse() * GetActorQuat()).Rotator();

	Chain->ApplyProxyTransform(SortedIndex, LocalLocation, LocalRotation);
}
