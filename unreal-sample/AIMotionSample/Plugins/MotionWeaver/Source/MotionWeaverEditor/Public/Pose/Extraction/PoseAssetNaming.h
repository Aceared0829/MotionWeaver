// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"

class UAnimSequence;

/**
 * 姿态资产的命名规则。
 *
 * 从 PoseExtractor.cpp 抽出来：那段逻辑有 90 行注释与边界处理，
 * 挤在抽取流程里会把「怎么抽」和「叫什么」搅在一起。
 * 单独成文件后，改命名规则不需要碰抽取逻辑，也方便单独验证。
 *
 * 规则：Pose_<动画名驼峰>_F<三位帧号>
 */
namespace PoseAssetNaming
{
	/** 名字前缀，让这类资产在内容浏览器里成组出现。 */
	extern const TCHAR* const Prefix;

	/**
	 * 从动画名生成资产名主干。
	 *
	 * 做四件事：
	 *  - 去掉常见前缀 M_ / A_ / AM_ / Anim_
	 *  - 空格与连字符统一成下划线
	 *  - 丢掉纯数字段（_1_0_ 这类版本号与站位编号）与方位标记段（F/L/R/B）
	 *  - 每段首字母大写后拼接（只对 ASCII 转大写，中文名不破坏编码）
	 *
	 * 长度上限 24 字符：资产名要落进文件路径，过长会在 CreateAsset 阶段失败。
	 * 全部被过滤时退回剥过前缀的原名，再退回 Unknown。
	 */
	FString BuildStem(const UAnimSequence* Sequence);

	/** 把主干与帧号拼成完整资产名的主干部分（不含唯一化后缀）。 */
	FString BuildBaseName(const UAnimSequence* Sequence, int32 Frame);
}
