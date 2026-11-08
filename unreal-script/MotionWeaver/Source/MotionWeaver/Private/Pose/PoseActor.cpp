// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/PoseActor.h"

#include "Pose/PoseProxyAsset.h"
#include "Pose/PoseComponent.h"

APoseActor::APoseActor()
{
	PrimaryActorTick.bCanEverTick = false;
	PrimaryActorTick.bStartWithTickEnabled = false;

	PoseComponent = CreateDefaultSubobject<UPoseComponent>(TEXT("PoseComponent"));
	RootComponent = PoseComponent;
}

UPoseProxyAsset* APoseActor::GetPoseAsset() const
{
	// 以组件为准：组件才是真正解析姿态、驱动骨骼的那一方。
	return PoseComponent != nullptr ? PoseComponent->PoseAsset : PoseAsset;
}

void APoseActor::SetPoseAsset(UPoseProxyAsset* InPoseAsset)
{
	// 属性本身也存一份，作为序列化镜像：组件尚未创建时 Getter 需要它。
	PoseAsset = InPoseAsset;

	if (PoseComponent == nullptr)
	{
		return;
	}

	// 组件已注册 = 渲染侧就绪，可以直接应用（运行时换资产、PIE、编辑器里手动刷新都走这条）。
	// 未注册 = 正处于构造/反序列化阶段，此刻做 SetSkinnedAssetAndUpdate 会因
	// SkinnedMeshComponent.cpp:2793 的注册检查而跳过骨骼分配；且细节面板提交后
	// 紧接着 PostEditChangeProperty 会重跑构造脚本把组件注销重注册一遍，做了也是白做。
	// 这种情况只登记，交给随后的 OnConstruction / OnRegister。
	if (PoseComponent->IsRegistered())
	{
		PoseComponent->ApplyPoseAsset(InPoseAsset);
	}
	else
	{
		PoseComponent->SetPoseAssetDeferred(InPoseAsset);
	}
}

void APoseActor::OnConstruction(const FTransform& Transform)
{
	Super::OnConstruction(Transform);

	// 构造脚本可能刚改过 PoseAsset。此处组件尚未注册，Setter 只会登记，
	// 真正的应用由随后的 OnRegister 完成，因此这里不需要再手动应用一次。
	SetPoseAsset(PoseAsset);
}

void APoseActor::PostLoad()
{
	Super::PostLoad();
	// 姿态与材质的应用留给组件的 OnRegister：那时渲染资源才就绪，
	// 且打包构建下构造脚本不会重跑，OnRegister 是唯一必经的入口。
}
