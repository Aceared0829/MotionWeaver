// Copyright ZhaoZining. All Rights Reserved.

#include "MotionWeaverPoseExtractor.h"

#include "MotionWeaverPoseAsset.h"
#include "MotionWeaverPoseTypes.h"

#include "AnimPreviewInstance.h"
#include "AnimationEditorUtils.h"
#include "Animation/AnimSequence.h"
#include "Animation/DebugSkelMeshComponent.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetToolsModule.h"
#include "Engine/SkeletalMesh.h"
#include "IPersonaToolkit.h"
#include "Materials/MaterialInterface.h"
#include "Misc/PackageName.h"
#include "Modules/ModuleManager.h"
#include "ReferenceSkeleton.h"
#include "ScopedTransaction.h"

#define LOCTEXT_NAMESPACE "MotionWeaverPoseExtractor"

namespace
{
	/** 取预览组件并确认处于暂停状态；不满足时填好原因。 */
	UDebugSkelMeshComponent* GetPausedPreviewComponent(IPersonaToolkit& Toolkit, FText& OutReason)
	{
		UDebugSkelMeshComponent* PreviewComponent = Toolkit.GetPreviewMeshComponent();
		if (PreviewComponent == nullptr)
		{
			OutReason = LOCTEXT("NoPreviewComponent", "当前编辑器没有可用的预览组件。");
			return nullptr;
		}

		UAnimPreviewInstance* PreviewInstance = PreviewComponent->PreviewInstance;
		if (PreviewInstance == nullptr)
		{
			OutReason = LOCTEXT("NoPreviewInstance", "当前预览没有动画实例，无法确定当前帧。");
			return nullptr;
		}

		// 正在播放时当前帧随时在变，抽出来的东西没有确定性，必须拒绝。
		if (PreviewInstance->IsPlaying())
		{
			OutReason = LOCTEXT("StillPlaying", "动画正在播放，请先暂停到需要的那一帧。");
			return nullptr;
		}

		return PreviewComponent;
	}
}

bool FMotionWeaverPoseExtractor::CanExtract(IPersonaToolkit& Toolkit, FText& OutReason)
{
	return GetPausedPreviewComponent(Toolkit, OutReason) != nullptr;
}

UMotionWeaverPoseAsset* FMotionWeaverPoseExtractor::ExtractToNewAsset(
	IPersonaToolkit& Toolkit, const FString& OuterPackagePath, FText& OutError)
{
	UDebugSkelMeshComponent* PreviewComponent = GetPausedPreviewComponent(Toolkit, OutError);
	if (PreviewComponent == nullptr)
	{
		return nullptr;
	}

	const USkeletalMesh* PreviewMesh = Cast<USkeletalMesh>(PreviewComponent->GetSkinnedAsset());
	if (PreviewMesh == nullptr)
	{
		// 预览组件还没绑网格时退回工具包记录的预览网格。
		PreviewMesh = Toolkit.GetPreviewMesh();
	}

	if (PreviewMesh == nullptr || PreviewMesh->GetRefSkeleton().GetNum() == 0)
	{
		OutError = LOCTEXT("NoMesh", "预览骨架为空，无法抽取姿态。");
		return nullptr;
	}

	const FReferenceSkeleton& RefSkeleton = PreviewMesh->GetRefSkeleton();
	const int32 BoneCount = RefSkeleton.GetNum();

	// 预览组件持有的是组件空间变换；快照按约定存局部空间。
	const TArray<FTransform>& ComponentSpace = PreviewComponent->GetComponentSpaceTransforms();
	if (ComponentSpace.Num() < BoneCount)
	{
		OutError = LOCTEXT("NotEvaluated", "预览还没有求值出骨骼变换，请先暂停并让视口刷新一次。");
		return nullptr;
	}

	// 事务对象必须可写：创建失败时要显式 Cancel。
	FScopedTransaction Transaction(LOCTEXT("ExtractPoseTransaction", "抽取姿势代理"));

	FAssetToolsModule& AssetToolsModule = FModuleManager::LoadModuleChecked<FAssetToolsModule>("AssetTools");

	FString PackageName;
	FString AssetName;
	AnimationEditorUtils::CreateUniqueAssetName(
		OuterPackagePath / PreviewMesh->GetName(), TEXT("_Pose"), PackageName, AssetName);

	UMotionWeaverPoseAsset* NewAsset = Cast<UMotionWeaverPoseAsset>(
		AssetToolsModule.Get().CreateAsset(
			AssetName,
			FPackageName::GetLongPackagePath(PackageName),
			UMotionWeaverPoseAsset::StaticClass(),
			nullptr));

	if (NewAsset == nullptr)
	{
		OutError = LOCTEXT("CreateFailed", "创建姿势代理资产失败。");
		Transaction.Cancel();
		return nullptr;
	}

	NewAsset->Modify();

	FMotionWeaverPoseSnapshot& Snapshot = NewAsset->Snapshot;
	Snapshot.Reset();
	Snapshot.SkeletonName = PreviewMesh->GetSkeleton() != nullptr
		? PreviewMesh->GetSkeleton()->GetFName()
		: NAME_None;
	Snapshot.BoneNames.Reserve(BoneCount);
	Snapshot.LocalTransforms.Reserve(BoneCount);

	// 需要骨骼索引、父索引与组件空间数组三者一起用，索引遍历比范围 for 更直接。
	for (int32 BoneIndex = 0; BoneIndex < BoneCount; ++BoneIndex)
	{
		Snapshot.BoneNames.Add(RefSkeleton.GetBoneName(BoneIndex));

		const FTransform& Self = ComponentSpace[BoneIndex];
		const int32 ParentIndex = RefSkeleton.GetParentIndex(BoneIndex);

		// 局部 = 自身组件空间 * 父骨组件空间的逆；根骨没有父骨，组件空间即局部空间。
		Snapshot.LocalTransforms.Add(
			ParentIndex >= 0 ? Self * ComponentSpace[ParentIndex].Inverse() : Self);
	}

	// 出处元数据：能拿到具体序列时才记帧号，避免记出无法复现的信息。
	UAnimPreviewInstance* PreviewInstance = PreviewComponent->PreviewInstance;
	const UAnimSequence* Sequence = PreviewInstance != nullptr ? PreviewInstance->GetAnimSequence() : nullptr;

	if (Sequence != nullptr && Sequence->GetNumberOfSampledKeys() > 0)
	{
		const FFrameRate SamplingRate = Sequence->GetSamplingFrameRate();

		Snapshot.SourceAnimation = FSoftObjectPath(Sequence);
		Snapshot.SourceTime = PreviewInstance->GetCurrentTime();
		Snapshot.SourceFrameRate = SamplingRate.AsDecimal();
		Snapshot.SourceTotalFrames = Sequence->GetNumberOfSampledKeys();
		Snapshot.SourceFrame = FMath::RoundToInt(Snapshot.SourceTime * Snapshot.SourceFrameRate);
	}

	NewAsset->Skeleton = PreviewMesh->GetSkeleton();
	NewAsset->PreviewMesh = const_cast<USkeletalMesh*>(PreviewMesh);

	// 记录材质覆盖，拖入关卡时按同一槽位顺序还原。
	for (UMaterialInterface* Material : PreviewComponent->GetMaterials())
	{
		NewAsset->PreviewMaterials.Add(Material);
	}

	NewAsset->MarkPackageDirty();
	FAssetRegistryModule::AssetCreated(NewAsset);

	return NewAsset;
}

#undef LOCTEXT_NAMESPACE
