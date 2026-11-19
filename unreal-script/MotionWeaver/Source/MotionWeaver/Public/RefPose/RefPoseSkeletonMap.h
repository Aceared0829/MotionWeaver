// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"

/**
 * 模型骨架 → UE 骨架的映射。
 *
 * 为什么必须显式存在：模型是 78 根 SOMA 命名（`Hips/Spine1/LeftArm/...`），
 * 项目里的 UE5 Mannequin 是 161 根 `root/pelvis/spine_01/...`。两套名字不同、
 * 数量不同，直接按槽位写骨骼会把手臂接成腿。映射必须是数据，不能藏在代码里猜。
 *
 * 覆盖面（本机实测）：62 根可一一对应（主链 + 手指 + Neck2）；剩余 16 根是
 * 面部（Jaw/LeftEye/RightEye/HeadEnd）与 UE5 Mannequin 不存在的末端骨
 * （10 根手指末端 + 2 根脚趾末端）。缺失的骨保持参考姿态，并在诊断里明确
 * 计数——不假装全部映射成功，也不把末端骨凑到父骨上。
 */
struct MOTIONWEAVER_API FRefPoseSkeletonMap
{
	/** 一根骨的对应关系。 */
	struct FBonePair
	{
		/** 模型槽位（0..BoneCount-1）。 */
		int32 ModelIndex = INDEX_NONE;

		/** UE 骨架槽位；INDEX_NONE 表示没有对应。 */
		int32 SkeletonIndex = INDEX_NONE;
	};

	/** 模型槽位 → UE 槽位；长度等于模型骨数，未映射项为 INDEX_NONE。 */
	TArray<int32> ModelToSkeleton;

	/** UE 槽位 → 模型槽位；长度等于 UE 骨数，未映射项为 INDEX_NONE。 */
	TArray<int32> SkeletonToModel;

	/** 映射上的模型骨数 / 总模型骨数，用于诊断。 */
	int32 MappedBoneCount = 0;
	int32 TotalModelBoneCount = 0;

	bool IsUsable() const { return MappedBoneCount > 0; }

	/**
	 * 用模型骨名 → UE 骨名的映射表建立索引。
	 *
	 * @param ModelBoneNames      模型骨名，顺序即槽位。
	 * @param SkeletonBoneNames   UE 骨架骨名，顺序即槽位。
	 * @param ModelToSkeletonName 模型骨名到 UE 骨名的对照表。
	 * @param OutUnmapped         没有映射到的模型骨名，按槽位顺序。
	 */
	static bool Build(
		TConstArrayView<FName> ModelBoneNames,
		TConstArrayView<FName> SkeletonBoneNames,
		const TMap<FName, FName>& ModelToSkeletonName,
		FRefPoseSkeletonMap& OutMap,
		TArray<FName>& OutUnmapped,
		FString& OutError);

	/**
	 * 把模型空间的逐帧局部变换重定向到 UE 骨架。
	 *
	 * 只改写有映射的骨；未映射的骨保持 UE 参考局部变换。平移长度统一取
	 * UE 骨架的参考骨长，避免 78 骨与 161 骨的骨长差异把角色拉变形。
	 *
	 * UE 骨架里可能存在未映射的中间骨（例如 Chest→Neck1 之间的
	 * spine_04/spine_05）。这些骨保持参考旋转，但会把子骨一起转走；
	 * 因此映射骨的局部旋转/平移要先扣掉这段参考旋转，才能让 UE 全局
	 * 方向与模型一致。父级映射必须与模型父级一致，否则直接报错。
	 */
	bool Retarget(
		TConstArrayView<TArray<FTransform>> ModelLocal,
		TConstArrayView<int32> ModelParents,
		TConstArrayView<FTransform> SkeletonReference,
		TConstArrayView<int32> SkeletonParents,
		TArray<TArray<FTransform>>& OutSkeletonLocal,
		FString& OutError) const;

	/**
	 * 默认映射表：模型命名 → UE5 Mannequin 命名。
	 *
	 * 只收录能一一对应的骨。面部与 `*End` 末端骨不强行凑对，
	 * 它们由调用方按缺失处理。
	 */
	static const TMap<FName, FName>& GetDefaultUe5Mapping();
};
