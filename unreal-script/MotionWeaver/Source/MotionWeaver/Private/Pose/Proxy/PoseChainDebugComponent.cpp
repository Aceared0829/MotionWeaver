// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Proxy/PoseChainDebugComponent.h"

#include "Animation/Skeleton.h"
#include "Pose/PoseProxyAsset.h"
#include "ReferenceSkeleton.h"

namespace
{
	const FColor TrajectoryColor(40, 160, 220);
	const FColor ArrowColor(120, 200, 255);
	const FColor KeyFigureColor(255, 200, 60);
	const FColor DerivedFigureColor(200, 200, 200);
	const FColor StopMarkerColor(240, 90, 60);
	const FColor DerivedMarkerColor(150, 150, 150);

	/** 简单的一帧调试场景代理：只往基类的数组里塞线段与箭头。 */
	class FChainSceneProxy final : public FDebugRenderSceneProxy
	{
	public:
		FChainSceneProxy(const UPrimitiveComponent* InComponent)
			: FDebugRenderSceneProxy(InComponent)
		{
			DrawType = FDebugRenderSceneProxy::WireMesh;
			DrawAlpha = 255;
		}

		virtual FPrimitiveViewRelevance GetViewRelevance(const FSceneView* View) const override
		{
			FPrimitiveViewRelevance Result;
			Result.bDrawRelevance = IsShown(View);
			Result.bDynamicRelevance = true;
			return Result;
		}
	};

	/** 把一条折线拆成线段塞进代理。 */
	void AddPolyline(FDebugRenderSceneProxy& Proxy, const TArray<FVector>& Points, const FColor& Color, float Thickness)
	{
		for (int32 Index = 1; Index < Points.Num(); ++Index)
		{
			Proxy.Lines.Emplace(Points[Index - 1], Points[Index], Color, Thickness);
		}
	}
}

UPoseChainDebugComponent::UPoseChainDebugComponent(const FObjectInitializer& ObjectInitializer)
	: Super(ObjectInitializer)
{
	PrimaryComponentTick.bCanEverTick = false;
	PrimaryComponentTick.bStartWithTickEnabled = false;
	// 这是纯可视化组件：不参与碰撞，也不该在游戏视图里挡住角色。
	SetCollisionEnabled(ECollisionEnabled::NoCollision);
	SetGenerateOverlapEvents(false);
}

void UPoseChainDebugComponent::SetChainData(
	const TArray<FChainTrajectorySample>& InSamples,
	const TArray<FPoseKey>& InKeys,
	const TArray<FDerivedKey>& InDerivedKeys,
	const TArray<FStickFigure>& InStickFigures,
	const FChainSettings& InSettings)
{
	Samples = InSamples;
	Keys = InKeys;
	DerivedKeys = InDerivedKeys;
	StickFigures = InStickFigures;
	Settings = InSettings;

	// 包围盒在这里算一次，CalcBounds 直接读：它在渲染线程被调用，
	// 不该再去遍历可能正被游戏线程改动的数组。
	LocalBounds = FBox(ForceInit);
	for (const FChainTrajectorySample& Sample : Samples)
	{
		LocalBounds += Sample.LocalLocation;
	}
	for (const FStickFigure& Figure : StickFigures)
	{
		LocalBounds += Figure.Origin;
		for (const FVector& Point : Figure.BonePoints)
		{
			LocalBounds += Figure.Origin + Point;
		}
	}
	if (!LocalBounds.IsValid)
	{
		LocalBounds = FBox(FVector(-50.0), FVector(50.0));
	}
	LocalBounds = LocalBounds.ExpandBy(100.0);

	// 重建场景代理才能在编辑器里立刻看到改动。
	MarkRenderStateDirty();
}

UPoseChainDebugComponent::FStickFigure UPoseChainDebugComponent::BuildStickFigure(
	const UPoseProxyAsset* PoseAsset, const FVector& Origin)
{
	FStickFigure Figure;
	Figure.Origin = Origin;

	if (PoseAsset == nullptr || !PoseAsset->IsValidPose())
	{
		return Figure;
	}

	const USkeleton* Skeleton = PoseAsset->Skeleton.LoadSynchronous();
	if (Skeleton == nullptr)
	{
		return Figure;
	}

	const FReferenceSkeleton& RefSkeleton = Skeleton->GetReferenceSkeleton();
	const int32 BoneCount = RefSkeleton.GetNum();
	if (BoneCount == 0)
	{
		return Figure;
	}

	const FPoseFrameSnapshot& Snapshot = PoseAsset->Snapshot;

	// 骨名 -> 局部变换。快照的骨序不一定和参考骨架一致，所以按名字建表。
	// 存在快照数组内部的指针：数组不能在本函数内被改动，因此指针不会失效。
	TMap<FName, const FTransform*> LocalByName;
	LocalByName.Reserve(Snapshot.BoneNames.Num());
	for (int32 Slot = 0; Slot < Snapshot.BoneNames.Num() && Slot < Snapshot.LocalTransforms.Num(); ++Slot)
	{
		LocalByName.Add(Snapshot.BoneNames[Slot], &Snapshot.LocalTransforms[Slot]);
	}

	// 按参考骨架顺序做一次 FK：父骨一定排在子骨之前，所以单遍就够。
	//
	// 关键点：局部变换是相对**父骨自身**的，因此累乘的必须是完整 FTransform，
	// 不能只把位置相加。只加位置等于忽略了父骨的旋转——手臂会朝世界某个固定方向
	// 甩出去，画出来是一团散开的线，和姿势毫无关系。
	//
	// 引擎的约定：组件空间 = 局部 * 父骨组件空间（FAnimationRuntime::FillUpComponentSpaceTransforms）。
	Figure.BonePoints.SetNumZeroed(BoneCount);
	Figure.ParentIndices.SetNumZeroed(BoneCount);

	TArray<FTransform> ComponentSpace;
	ComponentSpace.SetNum(BoneCount);

	for (int32 BoneIndex = 0; BoneIndex < BoneCount; ++BoneIndex)
	{
		const int32 ParentIndex = RefSkeleton.GetParentIndex(BoneIndex);
		Figure.ParentIndices[BoneIndex] = ParentIndex;

		const FName BoneName = RefSkeleton.GetBoneName(BoneIndex);
		const FTransform* const* LocalPtr = LocalByName.Find(BoneName);
		const FTransform* Local = (LocalPtr != nullptr) ? *LocalPtr : nullptr;

		// 快照里没有这根骨时退回参考姿态——宁可画出一个近似骨架，
		// 也不要留下一根指向原点的错误骨段。
		// GetRefBonePose() 的长度恒等于骨数，下标必在范围内。
		const FTransform LocalTransform = (Local != nullptr)
			? *Local
			: RefSkeleton.GetRefBonePose()[BoneIndex];

		ComponentSpace[BoneIndex] = (ParentIndex == INDEX_NONE)
			? LocalTransform
			: LocalTransform * ComponentSpace[ParentIndex];

		Figure.BonePoints[BoneIndex] = ComponentSpace[BoneIndex].GetLocation();
	}

	return Figure;
}

FDebugRenderSceneProxy* UPoseChainDebugComponent::CreateDebugSceneProxy()
{
	FChainSceneProxy* Proxy = new FChainSceneProxy(this);

	// 1) 稠密轨迹线。
	if (Samples.Num() >= 2)
	{
		TArray<FVector> Points;
		Points.Reserve(Samples.Num());
		for (const FChainTrajectorySample& Sample : Samples)
		{
			Points.Add(Sample.LocalLocation);
		}
		AddPolyline(*Proxy, Points, TrajectoryColor, 2.0f);
	}

	// 2) 方向箭头：按固定间隔取轨迹点，方向取该帧朝向。
	const int32 Stride = FMath::Max(1, Settings.ArrowStrideFrames);
	const double ArrowLength = FMath::Max(1.0, Settings.ArrowLengthCm);
	for (int32 Index = 0; Index < Samples.Num(); Index += Stride)
	{
		const FChainTrajectorySample& Sample = Samples[Index];
		const double YawRadians = FMath::DegreesToRadians(Sample.HeadingDegrees);
		const FVector Forward(FMath::Cos(YawRadians), FMath::Sin(YawRadians), 0.0);
		Proxy->ArrowLines.Emplace(
			Sample.LocalLocation,
			Sample.LocalLocation + Forward * ArrowLength,
			ArrowColor,
			6.0f);
	}

	// 3) 每个创作关键帧的火柴人。实心点标出关键帧自身，标了停的用另一色。
	//    BonePoints 为空的条目代表「这一条已经有真蒙皮在显示」，不必再画线。
	if (Settings.bDrawStickFigures)
	{
		for (int32 KeyIndex = 0; KeyIndex < StickFigures.Num(); ++KeyIndex)
		{
			const FStickFigure& Figure = StickFigures[KeyIndex];
			if (Figure.bHasSkinnedPose || Figure.BonePoints.Num() == 0)
			{
				continue;
			}

			const bool bIsStop = Keys.IsValidIndex(KeyIndex) && Keys[KeyIndex].bStopHere;
			const FColor Color = bIsStop ? StopMarkerColor : KeyFigureColor;

			for (int32 BoneIndex = 0; BoneIndex < Figure.BonePoints.Num(); ++BoneIndex)
			{
				const int32 ParentIndex = Figure.ParentIndices.IsValidIndex(BoneIndex)
					? Figure.ParentIndices[BoneIndex]
					: INDEX_NONE;
				if (ParentIndex == INDEX_NONE || !Figure.BonePoints.IsValidIndex(ParentIndex))
				{
					continue;
				}
				Proxy->Lines.Emplace(
					Figure.Origin + Figure.BonePoints[ParentIndex],
					Figure.Origin + Figure.BonePoints[BoneIndex],
					Color,
					1.5f);
			}

			Proxy->Spheres.Emplace(6.0f, Figure.Origin, FLinearColor(Color), FDebugRenderSceneProxy::WireMesh);
		}
	}
	else
	{
		// 不画火柴人时仍然标出每个 key 的落点，否则看不出关键帧在哪。
		for (int32 KeyIndex = 0; KeyIndex < StickFigures.Num(); ++KeyIndex)
		{
			const FStickFigure& Figure = StickFigures[KeyIndex];
			if (Figure.bHasSkinnedPose)
			{
				continue;
			}
			const bool bIsStop = Keys.IsValidIndex(KeyIndex) && Keys[KeyIndex].bStopHere;
			const FColor Color = bIsStop ? StopMarkerColor : KeyFigureColor;
			Proxy->Spheres.Emplace(3.0f, Figure.Origin, FLinearColor(Color),
				FDebugRenderSceneProxy::WireMesh);
		}
	}

	// 4) 自动插入的派生关键帧。空心小球，和作者摆的区分开——
	//    否则分不清哪些是自己摆的、哪些是求解器补的。
	for (const FDerivedKey& Derived : DerivedKeys)
	{
		Proxy->Spheres.Emplace(4.0f, Derived.LocalLocation, FLinearColor(DerivedMarkerColor),
			FDebugRenderSceneProxy::WireMesh);
	}

	return Proxy;
}

FBoxSphereBounds UPoseChainDebugComponent::CalcBounds(const FTransform& LocalToWorld) const
{
	// 渲染线程调用：只读在 SetChainData 里预先算好的那份，不碰样本数组。
	return FBoxSphereBounds(LocalBounds.TransformBy(LocalToWorld));
}
