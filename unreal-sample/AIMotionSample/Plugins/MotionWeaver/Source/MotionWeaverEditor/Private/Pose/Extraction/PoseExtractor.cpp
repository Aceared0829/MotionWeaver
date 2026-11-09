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
	/**
	 * 是否是动画命名里的方位标记（Front/Left/Right/Back）。
	 *
	 * 这些字母单独成段时（M_..._stand_F_Lfoot 里的 F）只是指明朝向与脚，
	 * 对识别「这段动画是什么」没有信息量，且会让名字变长。
	 * 但只认这四个：其他单字母可能是缩写（难度 A/B/C 等），不能一律丢。
	 */
	bool IsDirectionToken(const TCHAR Ch)
	{
		return Ch == TEXT('F') || Ch == TEXT('L') || Ch == TEXT('R') || Ch == TEXT('B')
			|| Ch == TEXT('f') || Ch == TEXT('l') || Ch == TEXT('r') || Ch == TEXT('b');
	}

	/**
	 * 从动画名生成资产名的主干。
	 *
	 * 动画名通常很长（M_Neutral_Traversal_Mantle_1_0_stand_F_Lfoot），
	 * 直接塞进资产名会超出可读长度。这里做四件事：
	 *  - 去掉常见前缀 M_ / A_ / AM_ / Anim_
	 *  - 把空格与连字符统一成下划线，兼容不同的命名风格
	 *  - 丢掉纯数字段（_1_0_ 这类版本号与站位编号）与方位标记段（F/L/R/B）
	 *  - 每段首字母大写后拼接，得到 NeutralTraversalMantleStandLfoot
	 *
	 * 只丢「单字母方位标记」，不是丢所有单字母段：
	 * 单字母也可能是缩写（如表示难度的 A/B/C），丢掉会损失信息。
	 * 方位标记是动画命名里的固定噪音，去掉不影响识别内容。
	 */
	FString BuildAnimationStem(const UAnimSequence* Sequence)
	{
		if (Sequence == nullptr)
		{
			return TEXT("Unknown");
		}

		FString Name = Sequence->GetName();

		// 剥掉常见前缀。按长度从长到短匹配，避免 A_ 先把 AM_ 吃掉。
		static const TCHAR* Prefixes[] = { TEXT("Anim_"), TEXT("AM_"), TEXT("M_"), TEXT("A_") };
		for (const TCHAR* Prefix : Prefixes)
		{
			if (Name.StartsWith(Prefix, ESearchCase::CaseSensitive))
			{
				Name.RightChopInline(FCString::Strlen(Prefix));
				break;
			}
		}

		// 统一分隔符：不同 DCC 导出的名字可能用空格或连字符。
		Name.ReplaceInline(TEXT(" "), TEXT("_"));
		Name.ReplaceInline(TEXT("-"), TEXT("_"));

		TArray<FString> RawSegments;
		Name.ParseIntoArray(RawSegments, TEXT("_"), /*InCullEmpty*/ true);

		FString Stem;
		for (const FString& Segment : RawSegments)
		{
			if (Segment.IsEmpty() || Segment.IsNumeric())
			{
				continue;
			}

			// 单个字母只在它是方位标记时丢弃。
			if (Segment.Len() == 1 && IsDirectionToken(Segment[0]))
			{
				continue;
			}

			// 首字母大写只对 ASCII 做。
			//
			// FChar::ToUpper 是按单个 TCHAR 处理的，中文/日文动画名
			// （M_角色_跑）的首字符是多字节序列的一部分，
			// 强行转大写会把编码写坏，得到乱码资产名。
			FString Part = Segment;
			if (Part.Len() > 0 && Part[0] < 128)
			{
				Part[0] = FChar::ToUpper(Part[0]);
			}
			Stem += Part;

			// 资产名要能落在文件路径里，过长会在 CreateAsset 阶段失败。
			//
			// 上限按「字符数」而不是「字节数」：中文名一个字算一个字符，
			// 但实际 32 个汉字已经有 96 字节，路径限制是按字节算的。
			// 这里保守取 24 字符，纯 ASCII 与宽字符都在安全范围内，
			// 同时仍然足够区分不同运动（8 个英文单词或 24 个汉字）。
			if (Stem.Len() >= 24)
			{
				break;
			}
		}

		// fallback 分两级，不能直接用 Sequence->GetName()：
		//
		// 1. 用剥过前缀的 Name（M_1_2_3 剥完是 1_2_3，至少不带那个类型前缀）
		// 2. Name 也会空（理论上不会，但空名字真的出现过），最后兜底 Unknown
		//
		// 之前直接返回 GetName()：动画名叫 M_1_2_3 时会拼出
		// Pose_M_1_2_3_F012——前缀又回来了，等于前面的剥前缀白做。
		if (!Stem.IsEmpty())
		{
			return Stem;
		}

		return Name.IsEmpty() ? TEXT("Unknown") : Name;
	}

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

	// 命名规则：Pose_<动画名>_F<帧号>（冲突时引擎自动加 _1 _2）。
	//
	// 为什么不用网格名：之前是 <网格名>_Pose，于是同一个角色抽十帧就得到
	// SKM_UEFN_Mannequin_Pose、_Pose1、_Pose2……在内容浏览器里完全分不出
	// 哪个是哪一帧、来自哪段动画，等于没有名字。
	//
	// 前缀 Pose_ 让这类资产在列表里成组出现，也便于按类型筛。
	// 帧号补零到三位，保证按名字排序时顺序正确（F009 排在 F010 前面）。
	// 出处信息只在这里算一次，命名与写进快照两处共用。
	//
	// 之前命名处算了一份、写快照处又算了一份，同一个事实存两遍：
	// 将来任何一处改了算法，资产名与实际帧号就会对不上，而且不会有任何报错。
	// 采样帧率为 0 时算不出帧号，退化为 F000 并在快照里留 INDEX_NONE。
	const FFrameRate SamplingRate = Sequence->GetSamplingFrameRate();
	const double CurrentTime = PreviewInstance->GetCurrentTime();
	const double SamplingRateDecimal = SamplingRate.AsDecimal();
	const int32 SourceFrame = SamplingRateDecimal > 0.0
		? FMath::RoundToInt(CurrentTime * SamplingRateDecimal)
		: 0;

	const FString AnimationStem = BuildAnimationStem(Sequence);
	const FString FrameSuffix = FString::Printf(TEXT("_F%03d"), SourceFrame);

	AnimationEditorUtils::CreateUniqueAssetName(
		OuterPackagePath / (TEXT("Pose_") + AnimationStem), FrameSuffix, PackageName, AssetName);

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
	// 帧号与帧率复用命名阶段那一份，不重新计算。
	if (Sequence->GetNumberOfSampledKeys() > 0)
	{
		Snapshot.SourceAnimation = FSoftObjectPath(Sequence);
		Snapshot.SourceTime = CurrentTime;
		Snapshot.SourceFrameRate = SamplingRateDecimal;
		Snapshot.SourceTotalFrames = Sequence->GetNumberOfSampledKeys();
		Snapshot.SourceFrame = SourceFrame;
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
