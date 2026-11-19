// Copyright ZhaoZining. All Rights Reserved.

#include "RefPose/RefPoseSkeletonMap.h"

bool FRefPoseSkeletonMap::Build(
	TConstArrayView<FName> ModelBoneNames,
	TConstArrayView<FName> SkeletonBoneNames,
	const TMap<FName, FName>& ModelToSkeletonName,
	FRefPoseSkeletonMap& OutMap,
	TArray<FName>& OutUnmapped,
	FString& OutError)
{
	OutMap = FRefPoseSkeletonMap();
	OutUnmapped.Reset();

	const int32 ModelCount = ModelBoneNames.Num();
	const int32 SkeletonCount = SkeletonBoneNames.Num();
	if (ModelCount <= 0 || SkeletonCount <= 0)
	{
		OutError = TEXT("骨架映射：模型或骨架没有骨骼。");
		return false;
	}

	OutMap.ModelToSkeleton.Init(INDEX_NONE, ModelCount);
	OutMap.SkeletonToModel.Init(INDEX_NONE, SkeletonCount);
	OutMap.TotalModelBoneCount = ModelCount;

	// UE 骨名查表：线性查找足够，骨数是百级，不必建哈希再引入大小写规则。
	auto FindSkeletonIndex = [&SkeletonBoneNames](FName Name) -> int32
	{
		for (int32 Index = 0; Index < SkeletonBoneNames.Num(); ++Index)
		{
			if (SkeletonBoneNames[Index] == Name)
			{
				return Index;
			}
		}
		return INDEX_NONE;
	};

	for (int32 ModelIndex = 0; ModelIndex < ModelCount; ++ModelIndex)
	{
		const FName ModelName = ModelBoneNames[ModelIndex];
		if (const FName* Target = ModelToSkeletonName.Find(ModelName))
		{
			const int32 SkeletonIndex = FindSkeletonIndex(*Target);
			if (SkeletonIndex == INDEX_NONE)
			{
				// 映射表里写了目标骨名，但 UE 骨架里没有：这是映射表与骨架不匹配，
				// 必须报出来，否则会静默丢骨。
				OutUnmapped.Add(ModelName);
				continue;
			}
			if (OutMap.SkeletonToModel[SkeletonIndex] != INDEX_NONE)
			{
				// 两根模型骨对到同一根 UE 骨：后写的会覆盖前一根，
				// 表现为「有一根骨永远不动」而没有任何报错。显式拒绝。
				OutError = FString::Printf(
					TEXT("骨架映射：模型骨 %s 与 %s 都映射到 UE 骨 %s。"),
					*ModelBoneNames[OutMap.SkeletonToModel[SkeletonIndex]].ToString(),
					*ModelName.ToString(),
					*Target->ToString());
				return false;
			}
			OutMap.ModelToSkeleton[ModelIndex] = SkeletonIndex;
			OutMap.SkeletonToModel[SkeletonIndex] = ModelIndex;
			++OutMap.MappedBoneCount;
			continue;
		}
		OutUnmapped.Add(ModelName);
	}

	if (OutMap.MappedBoneCount == 0)
	{
		OutError = TEXT("骨架映射：没有任何骨对上；模型与骨架不是同一套命名。");
		return false;
	}
	return true;
}

bool FRefPoseSkeletonMap::Retarget(
	TConstArrayView<TArray<FTransform>> ModelLocal,
	TConstArrayView<int32> ModelParents,
	TConstArrayView<FTransform> SkeletonReference,
	TConstArrayView<int32> SkeletonParents,
	TArray<TArray<FTransform>>& OutSkeletonLocal,
	FString& OutError) const
{
	if (!IsUsable())
	{
		OutError = TEXT("骨架映射为空，无法重定向。");
		return false;
	}
	if (SkeletonReference.Num() != SkeletonToModel.Num())
	{
		OutError = FString::Printf(
			TEXT("骨架参考变换数应有 %d 个，实际 %d。"),
			SkeletonToModel.Num(), SkeletonReference.Num());
		return false;
	}
	if (ModelParents.Num() != ModelToSkeleton.Num())
	{
		OutError = FString::Printf(
			TEXT("模型父索引数应有 %d 个，实际 %d。"),
			ModelToSkeleton.Num(), ModelParents.Num());
		return false;
	}
	if (SkeletonParents.Num() != SkeletonToModel.Num())
	{
		OutError = FString::Printf(
			TEXT("骨架父索引数应有 %d 个，实际 %d。"),
			SkeletonToModel.Num(), SkeletonParents.Num());
		return false;
	}

	const int32 SkeletonCount = SkeletonToModel.Num();

	// 未映射中间骨的参考旋转链：Q[s] 是从最近映射祖先到 s（含 s）的
	// 参考局部旋转乘积。映射骨自身 Q 为单位，因为它的局部旋转由模型覆盖。
	TArray<FQuat> ChainRotation;
	ChainRotation.SetNumUninitialized(SkeletonCount);
	TArray<int32> MappedAncestor;
	MappedAncestor.SetNumUninitialized(SkeletonCount);
	for (int32 SkeletonIndex = 0; SkeletonIndex < SkeletonCount; ++SkeletonIndex)
	{
		const int32 Parent = SkeletonParents[SkeletonIndex];
		const bool bMapped = SkeletonToModel[SkeletonIndex] != INDEX_NONE;
		if (bMapped)
		{
			ChainRotation[SkeletonIndex] = FQuat::Identity;
			MappedAncestor[SkeletonIndex] = SkeletonIndex;
			continue;
		}

		if (Parent < 0)
		{
			ChainRotation[SkeletonIndex] = SkeletonReference[SkeletonIndex].GetRotation();
			MappedAncestor[SkeletonIndex] = INDEX_NONE;
		}
		else
		{
			ChainRotation[SkeletonIndex] =
				ChainRotation[Parent] * SkeletonReference[SkeletonIndex].GetRotation();
			MappedAncestor[SkeletonIndex] = MappedAncestor[Parent];
		}
	}

	// 父级一致性：模型里 A 是 B 的父，UE 里 B 最近映射祖先也必须映射到 A。
	// 不满足说明映射表把两条不同的链接在了一起，继续算只会得到看似能动的错姿势。
	for (int32 ModelIndex = 0; ModelIndex < ModelToSkeleton.Num(); ++ModelIndex)
	{
		const int32 SkeletonIndex = ModelToSkeleton[ModelIndex];
		if (SkeletonIndex == INDEX_NONE)
		{
			continue;
		}
		const int32 ModelParent = ModelParents[ModelIndex];
		if (ModelParent < 0)
		{
			continue;
		}
		const int32 ExpectedSkeletonParent = ModelToSkeleton[ModelParent];
		if (ExpectedSkeletonParent == INDEX_NONE)
		{
			OutError = FString::Printf(
				TEXT("模型骨 %d 的父骨没有映射，无法确定重定向父级。"), ModelIndex);
			return false;
		}

		const int32 SkeletonParent = SkeletonParents[SkeletonIndex];
		const int32 ActualAncestor = SkeletonParent >= 0
			? MappedAncestor[SkeletonParent]
			: INDEX_NONE;
		if (ActualAncestor != ExpectedSkeletonParent)
		{
			OutError = FString::Printf(
				TEXT("骨架映射父级不一致：模型骨 %d 的父骨映射到 %d，但 UE 骨 %d 的最近映射祖先是 %d。"),
				ModelIndex, ExpectedSkeletonParent, SkeletonIndex, ActualAncestor);
			return false;
		}
	}

	OutSkeletonLocal.Reset(ModelLocal.Num());
	for (const TArray<FTransform>& ModelFrame : ModelLocal)
	{
		if (ModelFrame.Num() != ModelToSkeleton.Num())
		{
			OutError = FString::Printf(
				TEXT("模型帧骨数应有 %d 个，实际 %d。"),
				ModelToSkeleton.Num(), ModelFrame.Num());
			return false;
		}

		// 从参考姿态起步：未映射的骨（手指、面部、末端）保持参考，而不是零变换。
		TArray<FTransform>& Frame = OutSkeletonLocal.AddDefaulted_GetRef();
		Frame = SkeletonReference;

		for (int32 ModelIndex = 0; ModelIndex < ModelFrame.Num(); ++ModelIndex)
		{
			const int32 SkeletonIndex = ModelToSkeleton[ModelIndex];
			if (SkeletonIndex == INDEX_NONE)
			{
				continue;
			}

			const FTransform& Source = ModelFrame[ModelIndex];
			FTransform& Target = Frame[SkeletonIndex];
			const int32 SkeletonParent = SkeletonParents[SkeletonIndex];
			const bool bParentUnmapped = SkeletonParent >= 0
				&& SkeletonToModel[SkeletonParent] == INDEX_NONE;
			const FQuat ChainInverse = bParentUnmapped
				? ChainRotation[SkeletonParent].Inverse()
				: FQuat::Identity;

			// 平移长度取 UE 参考骨长：78 骨与 161 骨的骨长不同，
			// 直接沿用模型长度会把角色拉长或压扁。方向仍用模型给的。
			// 父级含未映射中间骨时，先把方向从最近映射祖先空间转回直接父级空间。
			const FVector SourceTranslation = ChainInverse.RotateVector(Source.GetTranslation());
			const FVector ReferenceTranslation = SkeletonReference[SkeletonIndex].GetTranslation();
			const double ReferenceLength = ReferenceTranslation.Size();
			const double SourceLength = SourceTranslation.Size();
			const FVector Direction = SourceLength > UE_KINDA_SMALL_NUMBER
				? SourceTranslation / SourceLength
				: ReferenceTranslation.GetSafeNormal();

			Target.SetRotation(ChainInverse * Source.GetRotation());
			Target.SetTranslation(Direction * ReferenceLength);
		}
	}
	return true;
}

const TMap<FName, FName>& FRefPoseSkeletonMap::GetDefaultUe5Mapping()
{
	// 映射来源：data/skeletons/mappings/ue_humanoid_to_soma_v1.json
	// （target_signature 215689…，对 161 骨 UE5 Mannequin 的 missing_mapped_bones 为空）。
	// 只写能确认一一对应的骨。面部（Jaw/LeftEye/RightEye/HeadEnd）和
	// UE 不存在的末端骨（*End）一律不写，让它们显式落进「未映射」，
	// 而不是猜错后看起来能动。
	static const TMap<FName, FName> Mapping = {
		{ TEXT("Root"), TEXT("root") },
		{ TEXT("Hips"), TEXT("pelvis") },
		{ TEXT("Spine1"), TEXT("spine_01") },
		{ TEXT("Spine2"), TEXT("spine_02") },
		{ TEXT("Chest"), TEXT("spine_03") },
		{ TEXT("Neck1"), TEXT("neck_01") },
		{ TEXT("Neck2"), TEXT("neck_02") },
		{ TEXT("Head"), TEXT("head") },
		{ TEXT("LeftShoulder"), TEXT("clavicle_l") },
		{ TEXT("LeftArm"), TEXT("upperarm_l") },
		{ TEXT("LeftForeArm"), TEXT("lowerarm_l") },
		{ TEXT("LeftHand"), TEXT("hand_l") },
		{ TEXT("LeftHandThumb1"), TEXT("thumb_01_l") },
		{ TEXT("LeftHandThumb2"), TEXT("thumb_02_l") },
		{ TEXT("LeftHandThumb3"), TEXT("thumb_03_l") },
		{ TEXT("LeftHandIndex1"), TEXT("index_metacarpal_l") },
		{ TEXT("LeftHandIndex2"), TEXT("index_01_l") },
		{ TEXT("LeftHandIndex3"), TEXT("index_02_l") },
		{ TEXT("LeftHandIndex4"), TEXT("index_03_l") },
		{ TEXT("LeftHandMiddle1"), TEXT("middle_metacarpal_l") },
		{ TEXT("LeftHandMiddle2"), TEXT("middle_01_l") },
		{ TEXT("LeftHandMiddle3"), TEXT("middle_02_l") },
		{ TEXT("LeftHandMiddle4"), TEXT("middle_03_l") },
		{ TEXT("LeftHandRing1"), TEXT("ring_metacarpal_l") },
		{ TEXT("LeftHandRing2"), TEXT("ring_01_l") },
		{ TEXT("LeftHandRing3"), TEXT("ring_02_l") },
		{ TEXT("LeftHandRing4"), TEXT("ring_03_l") },
		{ TEXT("LeftHandPinky1"), TEXT("pinky_metacarpal_l") },
		{ TEXT("LeftHandPinky2"), TEXT("pinky_01_l") },
		{ TEXT("LeftHandPinky3"), TEXT("pinky_02_l") },
		{ TEXT("LeftHandPinky4"), TEXT("pinky_03_l") },
		{ TEXT("RightShoulder"), TEXT("clavicle_r") },
		{ TEXT("RightArm"), TEXT("upperarm_r") },
		{ TEXT("RightForeArm"), TEXT("lowerarm_r") },
		{ TEXT("RightHand"), TEXT("hand_r") },
		{ TEXT("RightHandThumb1"), TEXT("thumb_01_r") },
		{ TEXT("RightHandThumb2"), TEXT("thumb_02_r") },
		{ TEXT("RightHandThumb3"), TEXT("thumb_03_r") },
		{ TEXT("RightHandIndex1"), TEXT("index_metacarpal_r") },
		{ TEXT("RightHandIndex2"), TEXT("index_01_r") },
		{ TEXT("RightHandIndex3"), TEXT("index_02_r") },
		{ TEXT("RightHandIndex4"), TEXT("index_03_r") },
		{ TEXT("RightHandMiddle1"), TEXT("middle_metacarpal_r") },
		{ TEXT("RightHandMiddle2"), TEXT("middle_01_r") },
		{ TEXT("RightHandMiddle3"), TEXT("middle_02_r") },
		{ TEXT("RightHandMiddle4"), TEXT("middle_03_r") },
		{ TEXT("RightHandRing1"), TEXT("ring_metacarpal_r") },
		{ TEXT("RightHandRing2"), TEXT("ring_01_r") },
		{ TEXT("RightHandRing3"), TEXT("ring_02_r") },
		{ TEXT("RightHandRing4"), TEXT("ring_03_r") },
		{ TEXT("RightHandPinky1"), TEXT("pinky_metacarpal_r") },
		{ TEXT("RightHandPinky2"), TEXT("pinky_01_r") },
		{ TEXT("RightHandPinky3"), TEXT("pinky_02_r") },
		{ TEXT("RightHandPinky4"), TEXT("pinky_03_r") },
		{ TEXT("LeftLeg"), TEXT("thigh_l") },
		{ TEXT("LeftShin"), TEXT("calf_l") },
		{ TEXT("LeftFoot"), TEXT("foot_l") },
		{ TEXT("LeftToeBase"), TEXT("ball_l") },
		{ TEXT("RightLeg"), TEXT("thigh_r") },
		{ TEXT("RightShin"), TEXT("calf_r") },
		{ TEXT("RightFoot"), TEXT("foot_r") },
		{ TEXT("RightToeBase"), TEXT("ball_r") },
	};
	return Mapping;
}
