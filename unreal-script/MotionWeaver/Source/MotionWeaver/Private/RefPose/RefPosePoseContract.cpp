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
		// 旋转的基变换：R' = Bᵀ · R · B。B 正交，逆等于转置。
		const FMatrix44d Matrix = Rotation.ToMatrix();
		return (Basis.GetTransposed() * Matrix * Basis).ToQuat();
	}
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
	FString& OutError)
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

	// 每根骨取一个「主子骨」作为 +X 朝向的锚点。分支骨（骨盆、胸、手）
	// 有多个孩子，选与参考 +X 最接近的那个，避免无谓扭转。
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

	// 参考骨长只算一次；它是 UE 骨架的唯一真值来源。
	TArray<double> ReferenceLengths;
	ReferenceLengths.SetNumUninitialized(BoneCount);
	for (int32 Bone = 0; Bone < BoneCount; ++Bone)
	{
		ReferenceLengths[Bone] = RefLocal[Bone].GetTranslation().Size();
	}

	for (const FRefPosePoseContract::FDecodedFrame& Frame : Decoded)
	{
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

				// 平移：把世界方向转回父级局部空间，再乘参考骨长。
				const FVector3d Translation =
					ParentGlobal.Inverse().RotateVector(UnitDirection * ReferenceLengthCm);

				// 朝向：UE 约定骨轴 +X 指向子骨。参考姿态给出扭转，位置给出方向。
				const FQuat4d BaseGlobal =
					ParentGlobal * FQuat4d(Reference.GetRotation());
				const FVector3d BaseAxis = BaseGlobal.RotateVector(FVector3d::ForwardVector);
				FQuat4d DesiredGlobal = BaseGlobal;

				int32 BestChild = INDEX_NONE;
				double BestDot = -2.0;
				for (const int32 Child : Children[Bone])
				{
					const FVector3d ChildDelta =
						Frame.PositionsMeters[Child] - Frame.PositionsMeters[Bone];
					const FVector3d ChildDirectionUe(
						ChildDelta.Z, -ChildDelta.X, ChildDelta.Y);
					if (ChildDirectionUe.IsNearlyZero(1e-9))
					{
						continue;
					}
					const double Dot = BaseAxis.GetSafeNormal().Dot(
						ChildDirectionUe.GetSafeNormal());
					if (Dot > BestDot)
					{
						BestDot = Dot;
						BestChild = Child;
					}
				}

				if (BestChild != INDEX_NONE && !BaseAxis.IsNearlyZero(1e-9))
				{
					const FVector3d ChildDelta =
						Frame.PositionsMeters[BestChild] - Frame.PositionsMeters[Bone];
					const FVector3d ChildDirectionUe(
						ChildDelta.Z, -ChildDelta.X, ChildDelta.Y);
					DesiredGlobal = FQuat4d::FindBetweenNormals(
						BaseAxis.GetSafeNormal(),
						ChildDirectionUe.GetSafeNormal()) * BaseGlobal;
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
			if (!bZeroDirection && !bZeroLength)
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
