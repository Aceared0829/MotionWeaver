// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Extraction/PoseAssetNaming.h"

#include "Animation/AnimSequence.h"

namespace PoseAssetNaming
{
	const TCHAR* const Prefix = TEXT("Pose_");

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
	}

	FString BuildStem(const UAnimSequence* Sequence)
	{
		if (Sequence == nullptr)
		{
			return TEXT("Unknown");
		}

		FString Name = Sequence->GetName();

		// 剥掉常见前缀。按长度从长到短匹配，避免 A_ 先把 AM_ 吃掉。
		static const TCHAR* Prefixes[] = { TEXT("Anim_"), TEXT("AM_"), TEXT("M_"), TEXT("A_") };
		for (const TCHAR* Each : Prefixes)
		{
			if (Name.StartsWith(Each, ESearchCase::CaseSensitive))
			{
				Name.RightChopInline(FCString::Strlen(Each));
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

			if (Stem.Len() >= 24)
			{
				break;
			}
		}

		// fallback 分两级，不能直接用 Sequence->GetName()：
		// 动画名叫 M_1_2_3 时，全段被过滤后若返回原名，前缀又回来了。
		if (!Stem.IsEmpty())
		{
			return Stem;
		}

		return Name.IsEmpty() ? TEXT("Unknown") : Name;
	}

	FString BuildBaseName(const UAnimSequence* Sequence, int32 Frame)
	{
		return FString::Printf(TEXT("%s%s_F%03d"), Prefix, *BuildStem(Sequence), Frame);
	}
}
