// Copyright ZhaoZining. All Rights Reserved.

#include "MotionWeaverPoseActor.h"

#include "MotionWeaverPoseAsset.h"
#include "MotionWeaverPoseComponent.h"

AMotionWeaverPoseActor::AMotionWeaverPoseActor()
{
	PrimaryActorTick.bCanEverTick = false;
	PrimaryActorTick.bStartWithTickEnabled = false;

	PoseComponent = CreateDefaultSubobject<UMotionWeaverPoseComponent>(TEXT("PoseComponent"));
	RootComponent = PoseComponent;
}

UMotionWeaverPoseAsset* AMotionWeaverPoseActor::GetPoseAsset() const
{
	// 以组件为准：组件才是真正解析姿态、驱动骨骼的那一方。
	return PoseComponent != nullptr ? PoseComponent->PoseAsset : PoseAsset;
}

void AMotionWeaverPoseActor::SetPoseAsset(UMotionWeaverPoseAsset* InPoseAsset)
{
	// 属性本身也存一份，作为序列化镜像：组件尚未创建时 Getter 需要它。
	PoseAsset = InPoseAsset;

	if (PoseComponent == nullptr)
	{
		return;
	}

	// 这里只登记，不做渲染侧的重活。
	// 细节面板提交会走 Setter，紧接着 AActor::PostEditChangeProperty 又会 RerunConstructionScripts
	// 把组件整个注销重注册一遍；若在此处直接 SetSkinnedAssetAndUpdate + RefreshBoneTransforms，
	// 同一次编辑会把渲染侧工作做两遍，而且夹在事务提交中间。
	// 实际应用由 OnConstruction / OnRegister 收口。
	PoseComponent->SetPoseAssetDeferred(InPoseAsset);
}

void AMotionWeaverPoseActor::OnConstruction(const FTransform& Transform)
{
	Super::OnConstruction(Transform);

	// 构造脚本可能刚改过 PoseAsset，这里正式落地一次（Setter 只做登记）。
	SetPoseAsset(PoseAsset);

	if (PoseComponent != nullptr)
	{
		PoseComponent->ApplyPoseAsset(PoseComponent->PoseAsset);
	}
}

void AMotionWeaverPoseActor::PostLoad()
{
	Super::PostLoad();
	// 姿态与材质的应用留给组件的 OnRegister：那时渲染资源才就绪，
	// 且打包构建下构造脚本不会重跑，OnRegister 是唯一必经的入口。
}
