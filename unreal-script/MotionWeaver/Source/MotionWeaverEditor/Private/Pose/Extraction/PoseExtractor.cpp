// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Extraction/PoseExtractor.h"

#include "Pose/PoseProxyAsset.h"
#include "Pose/PoseTypes.h"

#include "AnimPreviewInstance.h"
#include "AnimationEditorUtils.h"
#include "Animation/AnimSequence.h"
#include "Animation/DebugSkelMeshComponent.h"
#include "AssetToolsModule.h"
#include "Engine/SkeletalMesh.h"
#include "IPersonaToolkit.h"
#include "Materials/MaterialInterface.h"
#include "Misc/PackageName.h"
#include "Modules/ModuleManager.h"
#include "ReferenceSkeleton.h"

#define LOCTEXT_NAMESPACE "PoseExtractor"

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

bool FPoseExtractor::CanExtract(IPersonaToolkit& Toolkit, FText& OutReason)
{
	return GetPausedPreviewComponent(Toolkit, OutReason) != nullptr;
}

UPoseProxyAsset* FPoseExtractor::ExtractToNewAsset(
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

	// 必须先确认预览里有真实动画序列。
	// 只判断 IsPlaying() 不够：没有序列时抽出来的是参考姿态，
	// 那是"合法但没用"的产物，UI 还会报成功，比直接失败更糟。
	UAnimPreviewInstance* PreviewInstance = PreviewComponent->PreviewInstance;
	const UAnimSequence* Sequence = PreviewInstance != nullptr ? PreviewInstance->GetAnimSequence() : nullptr;
	if (Sequence == nullptr)
	{
		OutError = LOCTEXT("NoSequence", "当前预览没有动画序列，抽出来只会是参考姿态；请先选择一段动画。");
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

	// 这里不包 FScopedTransaction：建资产不是可撤销的用户编辑。
	// AssetTools::CreateAsset 内部会 NewObject(RF_Transactional) 并登记 AssetRegistry，
	// 引擎的建资产流程也都不包事务——包了以后 Ctrl+Z 会销毁对象但留下包与注册表记录，
	// 变成一个点不开的空资产。
	FAssetToolsModule& AssetToolsModule = FModuleManager::LoadModuleChecked<FAssetToolsModule>("AssetTools");

	FString PackageName;
	FString AssetName;
	AnimationEditorUtils::CreateUniqueAssetName(
		OuterPackagePath / PreviewMesh->GetName(), TEXT("_Pose"), PackageName, AssetName);

	UPoseProxyAsset* NewAsset = Cast<UPoseProxyAsset>(
		AssetToolsModule.Get().CreateAsset(
			AssetName,
			FPackageName::GetLongPackagePath(PackageName),
			UPoseProxyAsset::StaticClass(),
			nullptr));

	if (NewAsset == nullptr)
	{
		OutError = LOCTEXT("CreateFailed", "创建姿态代理资产失败。");
		return nullptr;
	}

	NewAsset->Modify();

	FPoseFrameSnapshot& Snapshot = NewAsset->Snapshot;
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

		// 局部 = 自身组件空间相对父骨组件空间；GetRelativeTransform 语义即 this * Other⁻¹，
		// 与引擎 AnimationRuntime 的 Local*Parent 互为逆运算。根骨没有父骨，组件空间即局部空间。
		Snapshot.LocalTransforms.Add(
			ParentIndex >= 0 ? Self.GetRelativeTransform(ComponentSpace[ParentIndex]) : Self);
	}

	// 出处元数据：前面已确认 Sequence 非空，这里只判断有没有采样帧。
	if (Sequence->GetNumberOfSampledKeys() > 0)
	{
		const FFrameRate SamplingRate = Sequence->GetSamplingFrameRate();

		Snapshot.SourceAnimation = FSoftObjectPath(Sequence);
		Snapshot.SourceTime = PreviewInstance->GetCurrentTime();
		Snapshot.SourceFrameRate = SamplingRate.AsDecimal();
		Snapshot.SourceTotalFrames = Sequence->GetNumberOfSampledKeys();
		Snapshot.SourceFrame = FMath::RoundToInt(Snapshot.SourceTime * Snapshot.SourceFrameRate);
	}

	// GetSkeleton() 返回 const 指针，与 TSoftObjectPtr 的非 const 赋值重载不匹配，
	// 直接赋会触发 C4996 弃用警告（UE 5.5 起）。这里显式去掉 const 再赋。
	if (const USkeleton* Skeleton = PreviewMesh->GetSkeleton())
	{
		NewAsset->Skeleton = const_cast<USkeleton*>(Skeleton);
	}

	NewAsset->PreviewMesh = const_cast<USkeletalMesh*>(PreviewMesh);

	// 记录材质覆盖，拖入关卡时按同一槽位顺序还原。
	for (UMaterialInterface* Material : PreviewComponent->GetMaterials())
	{
		NewAsset->PreviewMaterials.Add(Material);
	}

	// MarkPackageDirty 与 AssetCreated 已由 AssetTools::CreateAsset 内部完成，不重复调用。

	return NewAsset;
}

#undef LOCTEXT_NAMESPACE
