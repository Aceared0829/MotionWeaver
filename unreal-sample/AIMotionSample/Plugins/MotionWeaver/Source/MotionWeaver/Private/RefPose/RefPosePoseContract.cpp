// Copyright ZhaoZining. All Rights Reserved.

#include "RefPose/RefPosePoseContract.h"

namespace
{
	/**
	 * 米制 Y-up -> UE 的基变换。
	 *
	 * 与 data/runtime/refpose/__init__.py 的 MOTION_TO_UE 逐元素一致：
	 *   ue_x =  motion_z
	 *   ue_y = -motion_x
	 *   ue_z =  motion_y
	 * 两份实现必须同时改；Python 侧有测试，这里也有 C++ 测试。
	 *
	 * TMatrix 的四参数构造按「行」接收向量，所以这里传的是三行 + 平移行。
	 */
	const FMatrix44d MotionToUe(
		FVector3d(0.0, 0.0, 1.0),      // row 0
		FVector3d(-1.0, 0.0, 0.0),     // row 1
		FVector3d(0.0, 1.0, 0.0),      // row 2
		FVector3d(0.0, 0.0, 0.0));     // row 3

	const FMatrix44d UeToMotion = MotionToUe.GetTransposed();

	FQuat4d ChangeRotationBasis(const FQuat4d& Rotation, const FMatrix44d& Basis)
	{
		// 旋转的基变换：R' = B · R · B⁻¹ = B · R · Bᵀ。B 正交，逆等于转置。
		//
		// 这里曾经写成 Bᵀ·R·B，方向正好反了：往返仍然自洽（B·(BᵀRB)·Bᵀ = R），
		// 所以往返测试发现不了；但把「绕 UE +Z 的偏航」变成「绕模型 +X 的翻滚」，
		// 姿态组的 Heading 会把整个角色侧翻。判断依据不是公式自证，而是
		// 偏航在模型侧的轴向：motion ±Y 才是「上」。
		const FMatrix44d Matrix = Rotation.ToMatrix();
		return (Basis * Matrix * Basis.GetTransposed()).ToQuat();
	}

	/** 模型空间（米制 Y-up）的位移 → UE 组件空间（厘米）的轴向。 */
	FVector3d ModelDeltaToUe(const FVector3d& Delta)
	{
		return FVector3d(Delta.Z, -Delta.X, Delta.Y);
	}

	/**
	 * 把一个「骨轴 + 第二轴」的骨帧搬运到新的骨轴，得到唯一的全局旋转。
	 *
	 * 只用最短弧 `SwingBetween` 时，骨轴一旦接近静止轴的反方向，
	 * 最短弧的旋转轴就不唯一：相邻两帧可能一根选 +Y、一根选 -Y，
	 * 于是骨头的扭转每帧翻 180°（实测脚趾/小腿在硬参考帧处 179.9°）。
	 *
	 * 这里先用最短弧把骨轴对齐，再绕新骨轴补一个扭转角，把第二轴转回
	 * 「父级静止骨帧搬运过来」的投影方向。第二步只依赖当前骨轴与第二轴，
	 * 所以最短弧选了哪条轴都不影响最终结果，翻转消失。
	 */
	FQuat4d TransportFrame(
		const FVector3d& FromAxis,
		const FVector3d& FromSecond,
		const FVector3d& ToAxis,
		const FVector3d& ToSecond)
	{
		const FVector3d From = FromAxis.GetSafeNormal();
		const FVector3d To = ToAxis.GetSafeNormal();
		const FVector3d Second = FromSecond.GetSafeNormal();
		const FVector3d TargetSecond =
			(ToSecond - To * (ToSecond | To)).GetSafeNormal();
		FQuat4d Swing = FRefPosePoseContract::SwingBetween(From, To);
		if (Second.IsNearlyZero() || TargetSecond.IsNearlyZero())
		{
			return Swing;
		}
		// 搬运后的第二轴与新骨轴仍然正交（刚体旋转保角），只需补扭转。
		const FVector3d RotatedSecond = Swing.RotateVector(Second);
		const double Dot = FMath::Clamp(RotatedSecond | TargetSecond, -1.0, 1.0);
		const double Sin =
			FVector3d::CrossProduct(RotatedSecond, TargetSecond) | To;
		return FQuat4d(To, FMath::Atan2(Sin, Dot)) * Swing;
	}

}

FQuat4d FRefPosePoseContract::SwingBetween(const FVector3d& From, const FVector3d& To)
{
	const FVector3d A = From.GetSafeNormal();
	const FVector3d B = To.GetSafeNormal();
	if (A.IsNearlyZero() || B.IsNearlyZero())
	{
		return FQuat4d::Identity;
	}
	const double Dot = FMath::Clamp(A | B, -1.0, 1.0);
	if (Dot >= 1.0 - 1e-12)
	{
		return FQuat4d::Identity;
	}
	if (Dot <= -1.0 + 1e-9)
	{
		FVector3d Axis = FVector3d::CrossProduct(A, FVector3d(0.0, 0.0, 1.0));
		if (Axis.IsNearlyZero())
		{
			Axis = FVector3d::CrossProduct(A, FVector3d(1.0, 0.0, 0.0));
		}
		return FQuat4d(Axis.GetSafeNormal(), UE_PI);
	}
	return FQuat4d(
		FVector3d::CrossProduct(A, B).GetSafeNormal(), FMath::Acos(Dot));
}

FQuat4d FRefPosePoseContract::Decode6D(TConstArrayView<float> Values)
{
	check(Values.Num() >= RotationStride);

	// row-major 交错：m00,m01,m10,m11,m20,m21。
	//   col0 = values[0,2,4] = (m00, m10, m20)
	//   col1 = values[1,3,5] = (m01, m11, m21)
	// 第三列由叉乘补出，与 manifest 的 decode 声明一致。
	const double C0X = Values[0], C0Y = Values[2], C0Z = Values[4];
	const double C1X = Values[1], C1Y = Values[3], C1Z = Values[5];

	const double C2X = C0Y * C1Z - C0Z * C1Y;
	const double C2Y = C0Z * C1X - C0X * C1Z;
	const double C2Z = C0X * C1Y - C0Y * C1X;

	// 标准「矩阵 → 四元数」公式，按 R 的行列约定显式写：
	// R 的列是旋转后的基向量，即 R[Row][Col] 的 Col 是目标基方向。
	// 这里用 trace 分支，避免依赖引擎 TQuat(Matrix) 内部的行列约定。
	const double Trace = C0X + C1Y + C2Z;
	double QX, QY, QZ, QW;
	if (Trace > 0.0)
	{
		const double Scale = 2.0 * FMath::Sqrt(Trace + 1.0);
		QW = 0.25 * Scale;
		QX = (C1Z - C2Y) / Scale;
		QY = (C2X - C0Z) / Scale;
		QZ = (C0Y - C1X) / Scale;
	}
	else if (C0X > C1Y && C0X > C2Z)
	{
		const double Scale = 2.0 * FMath::Sqrt(1.0 + C0X - C1Y - C2Z);
		QW = (C1Z - C2Y) / Scale;
		QX = 0.25 * Scale;
		QY = (C1X + C0Y) / Scale;
		QZ = (C2X + C0Z) / Scale;
	}
	else if (C1Y > C2Z)
	{
		const double Scale = 2.0 * FMath::Sqrt(1.0 + C1Y - C0X - C2Z);
		QW = (C2X - C0Z) / Scale;
		QX = (C1X + C0Y) / Scale;
		QY = 0.25 * Scale;
		QZ = (C2Y + C1Z) / Scale;
	}
	else
	{
		const double Scale = 2.0 * FMath::Sqrt(1.0 + C2Z - C0X - C1Y);
		QW = (C0Y - C1X) / Scale;
		QX = (C2X + C0Z) / Scale;
		QY = (C2Y + C1Z) / Scale;
		QZ = 0.25 * Scale;
	}
	return FQuat4d(QX, QY, QZ, QW).GetNormalized();
}

void FRefPosePoseContract::Encode6D(const FQuat4d& Rotation, float* OutValues)
{
	check(OutValues != nullptr);
	// 从四元数直接展开旋转矩阵，再取 m00,m01,m10,m11,m20,m21。
	// 不用引擎的 ToMatrix：这里只需要三列，自算更可读也更易与 Python 对照。
	const double X = Rotation.X, Y = Rotation.Y, Z = Rotation.Z, W = Rotation.W;
	const double M00 = 1.0 - 2.0 * (Y * Y + Z * Z);
	const double M01 = 2.0 * (X * Y - Z * W);
	const double M10 = 2.0 * (X * Y + Z * W);
	const double M11 = 1.0 - 2.0 * (X * X + Z * Z);
	const double M20 = 2.0 * (X * Z - Y * W);
	const double M21 = 2.0 * (Y * Z + X * W);

	OutValues[0] = static_cast<float>(M00);
	OutValues[1] = static_cast<float>(M01);
	OutValues[2] = static_cast<float>(M10);
	OutValues[3] = static_cast<float>(M11);
	OutValues[4] = static_cast<float>(M20);
	OutValues[5] = static_cast<float>(M21);
}

FVector FRefPosePoseContract::ToUePosition(const FVector3d& PositionMeters)
{
	// 轴变换后统一乘 100，与 Python 的 `@ U.T * 100` 同序。
	const FVector3d UeMeters(PositionMeters.Z, -PositionMeters.X, PositionMeters.Y);
	return FVector(UeMeters * 100.0);
}

FVector3d FRefPosePoseContract::ToMotionPosition(const FVector& PositionUe)
{
	const FVector3d Meters = FVector3d(PositionUe) * 0.01;
	return FVector3d(-Meters.Y, Meters.Z, Meters.X);
}

FQuat4d FRefPosePoseContract::ToUeRotation(const FQuat4d& Rotation)
{
	return ChangeRotationBasis(Rotation, MotionToUe);
}

FQuat4d FRefPosePoseContract::ToMotionRotation(const FQuat4d& Rotation)
{
	return ChangeRotationBasis(Rotation, UeToMotion);
}

bool FRefPosePoseContract::DecodeWindow(
	TConstArrayView<float> Raw,
	int32 BoneCount,
	int32 FrameCount,
	TConstArrayView<float> Mean,
	TConstArrayView<float> Std,
	TArray<FDecodedFrame>& OutFrames,
	FString& OutError)
{
	if (BoneCount <= 0 || FrameCount <= 0)
	{
		OutError = TEXT("RefPose 解码：骨数或帧数无效。");
		return false;
	}
	if (Mean.Num() != BoneCount * PositionStride || Std.Num() != BoneCount * PositionStride)
	{
		OutError = FString::Printf(
			TEXT("RefPose 解码：统计量长度应为 %d，实际 mean=%d std=%d。"),
			BoneCount * PositionStride, Mean.Num(), Std.Num());
		return false;
	}
	const int64 Expected = static_cast<int64>(FrameCount) * BoneCount * BoneStride;
	if (Raw.Num() != Expected)
	{
		OutError = FString::Printf(
			TEXT("RefPose 解码：输出长度应为 %lld，实际 %d。"),
			static_cast<long long>(Expected), Raw.Num());
		return false;
	}

	OutFrames.Reset(FrameCount);
	for (int32 Frame = 0; Frame < FrameCount; ++Frame)
	{
		FDecodedFrame& Decoded = OutFrames.AddDefaulted_GetRef();
		Decoded.PositionsMeters.SetNumUninitialized(BoneCount);
		Decoded.Rotations.SetNumUninitialized(BoneCount);

		const float* FrameBase = Raw.GetData() + static_cast<int64>(Frame) * BoneCount * BoneStride;
		for (int32 Bone = 0; Bone < BoneCount; ++Bone)
		{
			const float* BoneBase = FrameBase + static_cast<int64>(Bone) * BoneStride;
			const float* MeanBase = Mean.GetData() + Bone * PositionStride;
			const float* StdBase = Std.GetData() + Bone * PositionStride;

			// 只有位置通道被归一化；旋转与 root_local 不归一化（见 manifest）。
			const FVector3d Denormalized(
				BoneBase[0] * StdBase[0] + MeanBase[0],
				BoneBase[1] * StdBase[1] + MeanBase[1],
				BoneBase[2] * StdBase[2] + MeanBase[2]);
			if (Denormalized.ContainsNaN())
			{
				OutError = FString::Printf(
					TEXT("RefPose 解码：帧 %d 骨 %d 位置含 NaN/Inf。"), Frame, Bone);
				return false;
			}
			Decoded.PositionsMeters[Bone] = Denormalized;

			Decoded.Rotations[Bone] = Decode6D(
				MakeArrayView(BoneBase + PositionStride, RotationStride));
		}
	}
	return true;
}

bool FRefPoseSkeletonSolver::Solve(
	TConstArrayView<int32> Parents,
	TConstArrayView<FTransform> RefLocal,
	TConstArrayView<FRefPosePoseContract::FDecodedFrame> Decoded,
	TArray<TArray<FTransform>>& OutLocal,
	FMetrics& OutMetrics,
	FString& OutError,
	TConstArrayView<bool> ChildUsable)
{
	const int32 BoneCount = Parents.Num();
	if (BoneCount <= 0)
	{
		OutError = TEXT("RefPose 解算：骨数为 0。");
		return false;
	}
	if (RefLocal.Num() != BoneCount)
	{
		OutError = FString::Printf(
			TEXT("RefPose 解算：参考局部变换数应为 %d，实际 %d。"), BoneCount, RefLocal.Num());
		return false;
	}

	// 父先子后是 UE 参考骨架的既有约定；显式检查，避免写出错误层级或越界。
	for (int32 Bone = 0; Bone < BoneCount; ++Bone)
	{
		const int32 Parent = Parents[Bone];
		if (Parent < -1 || Parent >= Bone)
		{
			OutError = FString::Printf(
				TEXT("RefPose 解算：骨 %d 的父索引 %d 非法（必须父先子后）。"), Bone, Parent);
			++OutMetrics.InvalidParents;
			return false;
		}
	}

	OutLocal.Reset(Decoded.Num());
	OutMetrics = FMetrics();

	// 每根骨取一个「主子骨」作为朝向锚点。分支骨（骨盆、胸、手）有多个孩子，
	// 选与参考骨轴 +X 最接近的那个：这样静止时主子骨就是 UE 骨骼本身指向
	// 的那条链，不会在骨盆上错误地跟着大腿走。
	TArray<TArray<int32>> Children;
	Children.SetNum(BoneCount);
	for (int32 Bone = 0; Bone < BoneCount; ++Bone)
	{
		const int32 Parent = Parents[Bone];
		if (Parent >= 0)
		{
			Children[Parent].Add(Bone);
		}
	}

	// 参考骨架的全局位置与全局旋转。
	//
	// RefLocal 的**旋转**是 UE 参考旋转，**平移长度**是模型契约骨长（见
	// BuildSolverReference）。骨架形状必须由 UE 旋转说了算：UE 约定每根骨
	// 的局部 +X 就是骨轴，因此参考位置只能按「父级全局旋转 × +X × 骨长」
	// 累加。
	//
	// 不能直接把 RefLocal 的平移当向量累加：那是模型空间（Y-up、每骨 +X）
	// 的父级相对偏移，与 UE 旋转的骨轴不是同一套方向。用错基准会让
	// `RestDirectionLocal` 变成随机方向，最短弧遇到接近反平行就翻 180°。
	TArray<FVector3d> RestPositions;
	TArray<FQuat4d> RestGlobalRotations;
	TArray<FVector3d> RestDirectionLocal;
	TArray<FVector3d> RestSecondLocal;
	TArray<int32> PrimaryChild;
	TArray<double> ReferenceLengths;
	RestPositions.SetNumUninitialized(BoneCount);
	RestGlobalRotations.SetNumUninitialized(BoneCount);
	RestDirectionLocal.SetNumUninitialized(BoneCount);
	RestSecondLocal.SetNumUninitialized(BoneCount);
	RestDirectionLocal.Init(FVector3d::ZeroVector, BoneCount);
	RestSecondLocal.Init(FVector3d::ZeroVector, BoneCount);
	ReferenceLengths.SetNumUninitialized(BoneCount);
	PrimaryChild.Init(INDEX_NONE, BoneCount);
	for (int32 Bone = 0; Bone < BoneCount; ++Bone)
	{
		const int32 Parent = Parents[Bone];
		const FVector3d LocalTranslation(RefLocal[Bone].GetTranslation());
		const FQuat4d LocalRotation =
			FQuat4d(RefLocal[Bone].GetRotation()).GetNormalized();
		ReferenceLengths[Bone] = LocalTranslation.Size();
		RestPositions[Bone] = Parent >= 0
			? RestPositions[Parent]
				+ RestGlobalRotations[Parent].RotateVector(
					FVector3d::ForwardVector * ReferenceLengths[Bone])
			: LocalTranslation;
		RestGlobalRotations[Bone] = Parent >= 0
			? (RestGlobalRotations[Parent] * LocalRotation).GetNormalized()
			: LocalRotation;
	}

	for (int32 Bone = 0; Bone < BoneCount; ++Bone)
	{
		if (Children[Bone].Num() == 0)
		{
			continue;
		}
		const FVector3d RestAxis =
			RestGlobalRotations[Bone].RotateVector(FVector3d::ForwardVector);
		double BestDot = -2.0;
		for (const int32 Child : Children[Bone])
		{
			// 目标骨架里没有的骨（模型 `*End`、面部）不能当朝向锚点：
			// 它们的位置不受任何绑定约束，模型预测一旦偏到另一侧，
			// 父骨就会整根翻 180°（实测 Head/Pinky4/ToeBase 都在硬参考
			// 帧前后翻过一次）。宁可让父骨保持静止朝向。
			if (ChildUsable.IsValidIndex(Child) && !ChildUsable[Child])
			{
				continue;
			}
			const FVector3d Direction =
				(RestPositions[Child] - RestPositions[Bone]).GetSafeNormal();
			const double Dot = Direction.IsNearlyZero() ? -2.0 : RestAxis | Direction;
			if (Dot > BestDot)
			{
				BestDot = Dot;
				PrimaryChild[Bone] = Child;
			}
		}
		if (PrimaryChild[Bone] != INDEX_NONE)
		{
			const FVector3d Direction =
				(RestPositions[PrimaryChild[Bone]] - RestPositions[Bone]).GetSafeNormal();
			// 把静止方向存进骨骼自身的静止坐标系：之后父级怎么转，都能用
			// BaseGlobal * RestDirectionLocal 还原「这根骨现在应该指向哪」。
			RestDirectionLocal[Bone] =
				RestGlobalRotations[Bone].Inverse().RotateVector(Direction);

			// 第二轴只用来约束扭转。取世界「上」在骨轴垂直平面上的投影；
			// 骨轴本身朝上时退化，再退到世界「右」。
			FVector3d Second =
				FVector3d::UpVector
				- RestDirectionLocal[Bone] * (FVector3d::UpVector | RestDirectionLocal[Bone]);
			if (Second.IsNearlyZero())
			{
				Second = FVector3d::RightVector
					- RestDirectionLocal[Bone]
						* (FVector3d::RightVector | RestDirectionLocal[Bone]);
			}
			RestSecondLocal[Bone] = Second.GetSafeNormal();
		}
	}

	for (int32 FrameIndex = 0; FrameIndex < Decoded.Num(); ++FrameIndex)
	{
		const FRefPosePoseContract::FDecodedFrame& Frame = Decoded[FrameIndex];
		if (Frame.PositionsMeters.Num() != BoneCount)
		{
			OutError = FString::Printf(
				TEXT("RefPose 解算：帧位置数应为 %d，实际 %d。"),
				BoneCount, Frame.PositionsMeters.Num());
			return false;
		}

		TArray<FTransform>& Local = OutLocal.AddDefaulted_GetRef();
		Local.SetNumUninitialized(BoneCount);

		// 父级全局旋转必须逐级累计：局部平移是在父级局部空间里表达的，
		// 直接用世界方向会让第二级以后的所有骨链按父级旋转再歪一次。
		TArray<FQuat4d> GlobalRotations;
		GlobalRotations.SetNumUninitialized(BoneCount);

		for (int32 Bone = 0; Bone < BoneCount; ++Bone)
		{
			const int32 Parent = Parents[Bone];
			const FTransform& Reference = RefLocal[Bone];
			const double ReferenceLengthCm = ReferenceLengths[Bone];

			// 根骨：位置交给 Root（Root 局部空间的原点）。
			// 模型里 Root 的位置是 -Rᵀ·k_pos，随片段移动；这里只用它做平移，
			// 保证「Root 轨道驱动整体位移」这条语义不被骨长逻辑吃掉。
			if (Parent < 0)
			{
				const FVector RootTranslation =
					FRefPosePoseContract::ToUePosition(Frame.PositionsMeters[Bone]);
				Local[Bone] = FTransform(Reference.GetRotation(), RootTranslation);
				GlobalRotations[Bone] = FQuat4d(Reference.GetRotation());
				const double RootLength = RootTranslation.Size();
				OutMetrics.MinBoneLengthCm = FMath::Min(OutMetrics.MinBoneLengthCm, RootLength);
				OutMetrics.MaxBoneLengthCm = FMath::Max(OutMetrics.MaxBoneLengthCm, RootLength);
				continue;
			}

			const FQuat4d ParentGlobal = GlobalRotations[Parent];

			// 方向来自位置差分：模型的位置是同一空间里的逐骨位置，
			// 相邻两点之差就是该骨在这一帧的朝向。
			const FVector3d DeltaMeters =
				Frame.PositionsMeters[Bone] - Frame.PositionsMeters[Parent];
			const FVector3d DirectionUe(DeltaMeters.Z, -DeltaMeters.X, DeltaMeters.Y);

			const bool bZeroDirection = DirectionUe.IsNearlyZero(1e-9);
			const bool bZeroLength = ReferenceLengthCm <= UE_KINDA_SMALL_NUMBER;
			if (bZeroDirection || bZeroLength)
			{
				++OutMetrics.DegenerateBones;
				Local[Bone] = Reference;
				GlobalRotations[Bone] = ParentGlobal * FQuat4d(Reference.GetRotation());
			}
			else
			{
				const FVector3d UnitDirection = DirectionUe.GetSafeNormal();

				// Root → 骨盆不是刚性骨链：模型里 Root 被钉在世界原点，
				// Hips 才在角色脚下；两者距离（实测 2.2–2.9 m）随片段变化，
				// 不能乘 UE 参考骨长，否则整个人会被拉到根骨附近。
				// 模型帧的原点就是骨盆在地面的水平投影，因此这里直接用
				// Hips 自身的模型位置（= 角色根到骨盆的偏移）。
				const bool bRootTrackLink = Parent == 0;
				const FVector3d LocalVector = bRootTrackLink
					? FVector3d(FRefPosePoseContract::ToUePosition(Frame.PositionsMeters[Bone]))
					: UnitDirection * ReferenceLengthCm;

				// 平移：把世界方向转回父级局部空间；刚性骨再乘参考骨长。
				const FVector3d Translation =
					ParentGlobal.Inverse().RotateVector(LocalVector);

				// 朝向：先取「父级当前朝向携带过来的静止骨帧」作为基准，再用
				// 最短弧把骨轴修正到当前主子骨方向。
				//
				// 基准只依赖当前父级与固定参考，不依赖上一帧、也不依赖窗口起点。
				// 之前用「上一帧解」播种，窗口首帧与硬参考帧会退回参考旋转：
				// 只要参考轴与当前方向接近反平行，最短弧就会翻 180°，
				// 于是每 24 帧、每个参考帧都抽一下（实测 179.6°/帧）。
				const FQuat4d RestLocal = (RestGlobalRotations[Parent].Inverse()
					* RestGlobalRotations[Bone]).GetNormalized();
				const FQuat4d BaseGlobal = (ParentGlobal * RestLocal).GetNormalized();
				FQuat4d DesiredGlobal = BaseGlobal;
				if (PrimaryChild[Bone] != INDEX_NONE)
				{
					const FVector3d ChildDelta =
						Frame.PositionsMeters[PrimaryChild[Bone]] - Frame.PositionsMeters[Bone];
					const FVector3d ChildDirectionUe =
						ModelDeltaToUe(ChildDelta);
					if (!ChildDirectionUe.IsNearlyZero(1e-9)
						&& !RestDirectionLocal[Bone].IsNearlyZero(1e-9))
					{
						const FVector3d BaseAxis =
							BaseGlobal.RotateVector(RestDirectionLocal[Bone]);
						const FVector3d BaseSecond =
							BaseGlobal.RotateVector(RestSecondLocal[Bone]);
						DesiredGlobal = TransportFrame(
							BaseAxis, BaseSecond, ChildDirectionUe, BaseSecond)
							* BaseGlobal;
					}
				}
				Local[Bone] = FTransform(
					ParentGlobal.Inverse() * DesiredGlobal,
					FVector(Translation));
				GlobalRotations[Bone] = DesiredGlobal;
			}

			const FVector Translation = Local[Bone].GetTranslation();

			const double LengthCm = Translation.Size();
			OutMetrics.MinBoneLengthCm = FMath::Min(OutMetrics.MinBoneLengthCm, LengthCm);
			OutMetrics.MaxBoneLengthCm = FMath::Max(OutMetrics.MaxBoneLengthCm, LengthCm);
			// Root → 骨盆是辅助链，长度本来就随片段变化，不参与骨长偏差统计。
			if (!bZeroDirection && !bZeroLength && Parent != 0)
			{
				OutMetrics.MaxBoneLengthDeviationCm = FMath::Max(
					OutMetrics.MaxBoneLengthDeviationCm,
					FMath::Abs(LengthCm - ReferenceLengthCm));
			}
		}

	}

	if (OutMetrics.MinBoneLengthCm == TNumericLimits<double>::Max())
	{
		OutMetrics.MinBoneLengthCm = 0.0;
	}
	return true;
}

bool FRefPoseSkeletonSolver::RebuildSkeletonPositions(
	TConstArrayView<int32> Parents,
	TConstArrayView<TArray<FTransform>> Local,
	TArray<TArray<FVector>>& OutPositionsCm,
	FString& OutError)
{
	const int32 BoneCount = Parents.Num();
	if (BoneCount <= 0)
	{
		OutError = TEXT("RefPose 自检：骨数为 0。");
		return false;
	}

	OutPositionsCm.Reset(Local.Num());
	for (const TArray<FTransform>& Frame : Local)
	{
		if (Frame.Num() != BoneCount)
		{
			OutError = FString::Printf(
				TEXT("RefPose 自检：帧骨数应为 %d，实际 %d。"), BoneCount, Frame.Num());
			return false;
		}

		TArray<FVector>& Positions = OutPositionsCm.AddDefaulted_GetRef();
		Positions.SetNumUninitialized(BoneCount);
		TArray<FQuat> Rotations;
		Rotations.SetNumUninitialized(BoneCount);

		for (int32 Bone = 0; Bone < BoneCount; ++Bone)
		{
			const int32 Parent = Parents[Bone];
			if (Parent < 0)
			{
				// 根骨的局部变换就是骨架空间变换。
				Positions[Bone] = Frame[Bone].GetTranslation();
				Rotations[Bone] = Frame[Bone].GetRotation();
			}
			else
			{
				Positions[Bone] = Positions[Parent]
					+ Rotations[Parent].RotateVector(Frame[Bone].GetTranslation());
				Rotations[Bone] = Rotations[Parent] * Frame[Bone].GetRotation();
			}
		}
	}
	return true;
}
