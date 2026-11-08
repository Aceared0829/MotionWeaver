// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Proxy/PoseChainActor.h"

#include "Components/SceneComponent.h"
#include "Core/MotionWeaverLog.h"
#include "Engine/World.h"
#include "Pose/PoseProxyAsset.h"
#include "Pose/PoseComponent.h"
#include "Pose/Proxy/PoseChain.h"
#include "Pose/Proxy/PoseChainDebugComponent.h"

#include "DrawDebugHelpers.h"
#include "Engine/Engine.h"
#include "Engine/SkinnedAsset.h"

#if WITH_EDITORONLY_DATA
#include "Pose/Proxy/PoseChainKeyProxy.h"
#endif

APoseChainActor::APoseChainActor()
{
	PrimaryActorTick.bCanEverTick = false;
	PrimaryActorTick.bStartWithTickEnabled = false;

	// 链 Actor 的变换就是整条链的原点；Keys 里的位置都相对它。
	USceneComponent* ChainRoot = CreateDefaultSubobject<USceneComponent>(TEXT("ChainRoot"));
	ChainRoot->SetMobility(EComponentMobility::Movable);
	RootComponent = ChainRoot;

	DebugComponent = CreateDefaultSubobject<UPoseChainDebugComponent>(TEXT("DebugComponent"));
	DebugComponent->SetupAttachment(ChainRoot);

	// 蒙皮组件在 SyncPoseComponents 里按需增删，这里不预建：
	// 预建一个只为了「有一个默认子对象」会带来一个永远空转的组件。
	// 代价是蓝图编辑器里看不组件树里的姿势——那些是运行期产物，本来也不该手工编辑。
}

void APoseChainActor::OnConstruction(const FTransform& Transform)
{
	Super::OnConstruction(Transform);

	// 构造脚本会改 Keys 与 Settings，重建一次让视口立刻反映新数据。
	if (bAutoSolve)
	{
		Rebuild();
	}
}

void APoseChainActor::PostLoad()
{
	Super::PostLoad();

	// 打包构建下 World 不重跑构造脚本，反序列化出来的 Actor 只走 PostLoad。
	// 蒙皮组件是 transient，读档后必然为空，必须在这里重建一次。
	if (!Result.Diagnostics.bSolved && Keys.Num() > 0)
	{
		Rebuild();
	}
	else
	{
		SyncPoseComponents(Keys);
	}
}

void APoseChainActor::SolveChain()
{
	Rebuild();
}

int32 APoseChainActor::AppendKey(
	UPoseProxyAsset* PoseAsset, const FVector& LocalLocation, double HeadingDegrees)
{
	FPoseKey NewKey;
	NewKey.PoseAsset = PoseAsset;
	NewKey.LocalLocation = LocalLocation;
	// 走成对 setter：HeadingDegrees 是派生的，单独写会让 BodyRotation 与它失配。
	NewKey.SetHeadingDegrees(FMath::Fmod(HeadingDegrees, 360.0));
	NewKey.Frame = 0;

	for (const FPoseKey& Existing : Keys)
	{
		NewKey.Frame = FMath::Max(NewKey.Frame, Existing.Frame + FMath::Max(1, Settings.NewKeyFrameStride));
	}

	const int32 NewIndex = Keys.Add(NewKey);
	Rebuild();
	return NewIndex;
}

void APoseChainActor::Rebuild()
{
	Result.Reset();
	FPoseChainSolver::Solve(Keys, Settings, Result);

	// 求解按帧号排过序；姿势组件必须跟着同一个顺序，否则姿势会和轨迹错位。
	// 同时记下「排序后下标 -> Keys 原始下标」，编辑器手柄要靠它把选中的网格
	// 翻译回 Keys 里的第几条——作者在细节面板里调换顺序时这个映射不会失效。
	SortedToOriginal.SetNum(Keys.Num());
	for (int32 Index = 0; Index < Keys.Num(); ++Index)
	{
		SortedToOriginal[Index] = Index;
	}

	SortedToOriginal.StableSort([this](int32 A, int32 B)
	{
		return Keys[A].Frame < Keys[B].Frame;
	});

	TArray<FPoseKey> SortedKeys;
	SortedKeys.Reserve(SortedToOriginal.Num());
	for (int32 OriginalIndex : SortedToOriginal)
	{
		SortedKeys.Add(Keys[OriginalIndex]);
	}
	SortedKeysCache = SortedKeys;

	// 先建代理，再同步蒙皮：SyncPoseComponents 要根据「代理是否真的存在」
	// 决定要不要把链上蒙皮让出去。顺序反了就会读到上一帧的旧值。
#if WITH_EDITORONLY_DATA
	RebuildProxyActors();
	DrawProxyDebug();
#endif

	SyncPoseComponents(SortedKeys);

	if (DebugComponent != nullptr)
	{
		// 有真蒙皮的条目不再画骨架线，否则线会穿在角色身上。
		TArray<UPoseChainDebugComponent::FStickFigure> Figures;
		Figures.Reserve(SortedKeys.Num());
		for (const FPoseKey& Key : SortedKeys)
		{
			const bool bHasSkinnedPose = Settings.bShowSkinnedPoses
				&& Key.PoseAsset != nullptr
				&& !Key.PoseAsset->PreviewMesh.IsNull();

			UPoseChainDebugComponent::FStickFigure Figure;
			if (bHasSkinnedPose)
			{
				// 只在落点留一个标记，骨点不填。
				Figure.Origin = Key.LocalLocation;
				Figure.bHasSkinnedPose = true;
			}
			else
			{
				Figure = UPoseChainDebugComponent::BuildStickFigure(Key.PoseAsset, Key.LocalLocation);
			}
			Figures.Add(MoveTemp(Figure));
		}

		DebugComponent->SetVisibility(bShowChain);
		DebugComponent->SetChainData(Result.Samples, SortedKeys, Result.DerivedKeys, Figures, Settings);
	}

	if (!Result.Diagnostics.IsValid() && Result.Diagnostics.Errors.Num() > 0)
	{
		UE_LOG(LogMotionWeaver, Warning,
			TEXT("[%s] 姿态链未通过：%s"), *GetName(), *Result.Diagnostics.Errors[0]);
	}
}

void APoseChainActor::ApplyProxyTransform(
	int32 SortedIndex, const FVector& NewLocalLocation, const FRotator& NewBodyRotation)
{
	if (!SortedToOriginal.IsValidIndex(SortedIndex))
	{
		return;
	}

	const int32 OriginalIndex = SortedToOriginal[SortedIndex];
	if (!Keys.IsValidIndex(OriginalIndex))
	{
		return;
	}

	FPoseKey& Key = Keys[OriginalIndex];
	const bool bLocationChanged = !Key.LocalLocation.Equals(NewLocalLocation, 0.01);
	const bool bRotationChanged = !Key.BodyRotation.Equals(NewBodyRotation, 0.05);

	// 引擎在拖动过程中会反复调 PostEditMove，代理侧已经挡过一次，
	// 这里再挡一次：数值没变就不要重算整条链。
	if (!bLocationChanged && !bRotationChanged)
	{
		return;
	}

	// 先记下旧偏航再写新值：SetBodyRotation 会同步刷新 HeadingDegrees，
	// 写完之后再比较就永远相等，轨迹不会重算。
	const double OldHeading = Key.HeadingDegrees;

	Key.LocalLocation = NewLocalLocation;
	Key.SetBodyRotation(NewBodyRotation);

	const bool bHeadingChanged = !FMath::IsNearlyEqual(OldHeading, Key.HeadingDegrees, 0.05);

	// Pitch/Roll 不参与 Root 求解，偏航没动就没必要重解轨迹；
	// 但网格要跟着转，所以只同步显示。
	if (!bLocationChanged && !bHeadingChanged)
	{
		SyncDisplayOnly();
		return;
	}

	Rebuild();
}

void APoseChainActor::SyncDisplayOnly()
{
	// 不重解轨迹，只把当前 BodyRotation 推给显示层。
	// 触发场景：拖旋转 gizmo 但只改了 Pitch/Roll——Root 路径没变，
	// 重算一遍采样纯属浪费，而且会让拖动变卡。
	for (int32 Index = 0; Index < PoseComponents.Num() && Index < SortedKeysCache.Num(); ++Index)
	{
		UPoseComponent* Component = PoseComponents[Index];
		if (Component == nullptr)
		{
			continue;
		}

		const FPoseKey& Key = SortedKeysCache[Index];
		Component->SetRelativeLocationAndRotation(Key.LocalLocation, Key.BodyRotation);
	}

#if WITH_EDITORONLY_DATA
	SyncProxyTransforms();
#endif

	if (DebugComponent != nullptr)
	{
		DebugComponent->MarkRenderStateDirty();
	}
}

void APoseChainActor::SyncPoseComponents(const TArray<FPoseKey>& SortedKeys)
{
	USceneComponent* ChainRoot = RootComponent;
	if (ChainRoot == nullptr)
	{
		return;
	}

	UWorld* World = GetWorld();

	// 组件数量对齐到 key 数量。多出来的先摘掉再从数组移除：
	// 直接清数组会让组件变成孤儿，渲染状态与场景代理都不会被释放。
	while (PoseComponents.Num() > SortedKeys.Num())
	{
		if (UPoseComponent* Extra = PoseComponents.Pop())
		{
			Extra->DestroyComponent();
		}
	}

	for (int32 Index = 0; Index < SortedKeys.Num(); ++Index)
	{
		const FPoseKey& Key = SortedKeys[Index];

		if (!PoseComponents.IsValidIndex(Index) || PoseComponents[Index] == nullptr)
		{
			// 没在世界里的 Actor（例如构造脚本早期）不创建组件：
			// NewObject 出来的组件没有注册，写骨骼会静默失败。
			if (World == nullptr)
			{
				continue;
			}

			UPoseComponent* NewComponent =
				NewObject<UPoseComponent>(this, UPoseComponent::StaticClass());
			if (NewComponent == nullptr)
			{
				continue;
			}

			NewComponent->SetupAttachment(ChainRoot);
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

		// 位置与朝向都由 key 决定；蒙皮网格本身不带根位移。
		// 这里用完整 BodyRotation（不是只有 Yaw）：求解器不关心 Pitch/Roll，
		// 但画出来的角色必须和作者在视口里转出来的姿态一致。
		Component->SetRelativeLocationAndRotation(Key.LocalLocation, Key.BodyRotation);

		// 只有代理真的存在时，才把显示与可点让给它：
		// 两边同时渲染会让同一姿势画两遍（Z-fighting），
		// 而点到不可交互的那个组件时 gizmo 依旧出不来。
		//
		// 但绝不能在「代理一个都没生成」的时候先把蒙皮关掉——
		// 那会两头空：既看不见角色，也点不中任何东西。这是必须由数据决定的，
		// 不能只看配置开关。
		const bool bProxiesTakeOver =
#if WITH_EDITORONLY_DATA
			bShowEditableProxies && ProxyActors.Num() > 0;
#else
			false;
#endif

		const bool bShouldShow = bShowChain
			&& Settings.bShowSkinnedPoses
			&& !bProxiesTakeOver
			&& Key.PoseAsset != nullptr
			&& !Key.PoseAsset->PreviewMesh.IsNull();

		Component->SetVisibility(bShouldShow);

		// 只在真的显示时才加载网格与材质：关掉蒙皮就不该为它付加载代价。
		if (bShouldShow)
		{
			Component->ApplyPoseAsset(Key.PoseAsset);
		}
	}
}

#if WITH_EDITOR
void APoseChainActor::PostEditChangeProperty(FPropertyChangedEvent& PropertyChangedEvent)
{
	Super::PostEditChangeProperty(PropertyChangedEvent);

	// 不按属性名过滤：编辑 Keys 里某个成员的坐标时，事件里的名字是 "LocalLocation"
	// 而不是 "Keys"，白名单会漏掉最常见的那种改动。宁可多算一次，也不能改了没反应——
	// 求解是纯内存计算，一次几毫秒，重算不心疼。
	if (bAutoSolve)
	{
		Rebuild();
	}
	else
	{
		// 自动求解关掉时，至少让显隐开关立刻生效。
		if (DebugComponent != nullptr)
		{
			DebugComponent->SetVisibility(bShowChain);
		}
		for (UPoseComponent* Component : PoseComponents)
		{
			if (Component != nullptr)
			{
				Component->SetVisibility(bShowChain && Settings.bShowSkinnedPoses);
			}
		}
	}
}
#endif

#if WITH_EDITORONLY_DATA
void APoseChainActor::RebuildProxyActors()
{
	// 构造函数 / CDO 阶段没有世界，也还没有根组件：那时创建 Actor 会静默失败。
	UWorld* World = GetWorld();
	if (World == nullptr || RootComponent == nullptr || HasAnyFlags(RF_ClassDefaultObject))
	{
		UE_LOG(LogMotionWeaver, Verbose,
			TEXT("[%s] 跳过代理生成：World=%d Root=%d CDO=%d"),
			*GetName(), World != nullptr ? 1 : 0, RootComponent != nullptr ? 1 : 0,
			HasAnyFlags(RF_ClassDefaultObject) ? 1 : 0);
		return;
	}

	const int32 Wanted = SortedKeysCache.Num();

	// 代理是「点得中、拖得动」的唯一载体。生成失败时不能静默：
	// 视口里表现成「怎么点都没反应」，而这正是最难自查的一类故障。
	UE_LOG(LogMotionWeaver, Log,
		TEXT("[%s] 重建代理：keys=%d 现有代理=%d 世界=%s"),
		*GetName(), Wanted, ProxyActors.Num(), *World->GetName());

	// 多出来的先销毁：类型与 key 数量无关，纯粹是「这一帧被删了」。
	while (ProxyActors.Num() > Wanted)
	{
		if (APoseChainKeyProxy* Extra = ProxyActors.Pop())
		{
			if (IsValid(Extra))
			{
				Extra->Destroy();
			}
		}
	}

	const bool bVisible = bShowChain && bShowEditableProxies;

	for (int32 Index = 0; Index < Wanted; ++Index)
	{
		const FPoseKey& Key = SortedKeysCache[Index];

		// 蒙皮资产为空时不给代理挂网格：代理的价值是能点能拖，
		// 没有网格的话点不到，留着只会是个看不见的空 Actor。
		const bool bHasMesh = Key.PoseAsset != nullptr && !Key.PoseAsset->PreviewMesh.IsNull();

		if (!ProxyActors.IsValidIndex(Index) || !IsValid(ProxyActors[Index]))
		{
			FActorSpawnParameters Params;
			Params.Owner = this;
			Params.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
			Params.ObjectFlags |= RF_Transient;

			APoseChainKeyProxy* NewProxy = World->SpawnActor<APoseChainKeyProxy>(
				APoseChainKeyProxy::StaticClass(), FTransform::Identity, Params);
			if (NewProxy == nullptr)
			{
				UE_LOG(LogMotionWeaver, Error,
					TEXT("[%s] 代理生成失败（第 %d 个 key）：视口里将点不中这一帧。"),
					*GetName(), Index);
				continue;
			}

			UE_LOG(LogMotionWeaver, Log, TEXT("[%s] 生成代理 %s（key %d）"),
				*GetName(), *NewProxy->GetName(), Index);

			// 挂到链上：拖整条链时代理跟着走，不需要每帧同步。
			NewProxy->AttachToActor(this, FAttachmentTransformRules::KeepRelativeTransform);
			NewProxy->OwningChain = this;
			NewProxy->SortedIndex = Index;

			if (ProxyActors.IsValidIndex(Index))
			{
				ProxyActors[Index] = NewProxy;
			}
			else
			{
				ProxyActors.Add(NewProxy);
			}
		}

		APoseChainKeyProxy* Proxy = ProxyActors[Index];
		if (Proxy == nullptr)
		{
			continue;
		}

		Proxy->SortedIndex = Index;
		Proxy->SetActorHiddenInGame(false);
		Proxy->SetActorEnableCollision(false);
		Proxy->SyncFromChain(Key.LocalLocation, Key.BodyRotation, Key.PoseAsset, bVisible && bHasMesh, true);
	}
}

void APoseChainActor::DrawProxyDebug() const
{
	if (!bShowProxyDebug || GEngine == nullptr)
	{
		return;
	}

	const UWorld* World = GetWorld();
	if (World == nullptr)
	{
		return;
	}

	// 顶部一行总账：代理数量对不上时，一眼就知道是没生成还是生成了没显示。
	GEngine->AddOnScreenDebugMessage(
		reinterpret_cast<uint64>(this) + 1, 0.0f, FColor::Cyan,
		FString::Printf(TEXT("[%s] keys=%d proxies=%d show=%d"),
			*GetName(), SortedKeysCache.Num(), ProxyActors.Num(), bShowEditableProxies ? 1 : 0));

	// 逐个代理：位置、可见性、网格名。世界空间文字直接落在它头上，
	// 视口里能立刻看出「这个 key 到底有没有一个能点的东西」。
	for (int32 Index = 0; Index < ProxyActors.Num(); ++Index)
	{
		const APoseChainKeyProxy* Proxy = ProxyActors[Index];
		if (Proxy == nullptr)
		{
			continue;
		}

		FString MeshName = TEXT("NONE");
		bool bCompVisible = false;
		FVector Extent = FVector::ZeroVector;

		if (const UPoseComponent* Comp = Proxy->PoseDisplay)
		{
			bCompVisible = Comp->IsVisible();
			Extent = Comp->Bounds.BoxExtent;
			if (const USkinnedAsset* Asset = Comp->GetSkinnedAsset())
			{
				MeshName = Asset->GetName();
			}
		}

		const FVector TextAt = Proxy->GetActorLocation() + FVector(0.0, 0.0, 200.0);
		DrawDebugString(World, TextAt,
			FString::Printf(TEXT("k%d %s vis=%d ext=%.0f"),
				Index, *MeshName, bCompVisible ? 1 : 0, Extent.GetMax()),
			nullptr, FColor::Yellow, 0.0f, true, 1.4f);
	}
}

void APoseChainActor::SyncProxyTransforms()
{
	const bool bVisible = bShowChain && bShowEditableProxies;

	for (int32 Index = 0; Index < ProxyActors.Num() && Index < SortedKeysCache.Num(); ++Index)
	{
		APoseChainKeyProxy* Proxy = ProxyActors[Index];
		if (Proxy == nullptr)
		{
			continue;
		}

		const FPoseKey& Key = SortedKeysCache[Index];
		const bool bHasMesh = Key.PoseAsset != nullptr && !Key.PoseAsset->PreviewMesh.IsNull();
		Proxy->SortedIndex = Index;
		Proxy->SyncFromChain(Key.LocalLocation, Key.BodyRotation, Key.PoseAsset, bVisible && bHasMesh, false);
	}
}
#endif
