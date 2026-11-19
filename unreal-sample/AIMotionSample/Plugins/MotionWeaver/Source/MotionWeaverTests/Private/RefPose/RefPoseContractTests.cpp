// Copyright ZhaoZining. All Rights Reserved.

#include "CoreMinimal.h"
#include "Animation/Skeleton.h"
#include "Engine/SkeletalMesh.h"
#include "Misc/AutomationTest.h"
#include "UObject/StrongObjectPtr.h"
#include "UObject/SoftObjectPath.h"
#include "Pose/Data/PoseGroupProxyAsset.h"
#include "Pose/Data/PoseProxyAsset.h"
#include "Pose/Data/PoseTypes.h"
#include "RefPose/RefPoseGenerator.h"
#include "RefPose/RefPoseGroupGenerator.h"
#include "RefPose/RefPosePoseContract.h"
#include "RefPose/RefPoseModel.h"
#include "RefPose/RefPoseSkeletonMap.h"
#include "RefPose/RefPoseWindowBuilder.h"

#if WITH_DEV_AUTOMATION_TESTS

/**
 * 坐标契约与装配的可判定回归。
 *
 * 这些测试只用合成数据，不依赖外部数据根：它们验证「公式本身」。
 * 真实数据的端到端一致性由 MotionWeaver.RefPose.NNE 那一组负责。
 */
namespace
{
	constexpr int32 TestBoneCount = 4;

	/** 父先子后的四骨链：0 根，1 接 0，2 接 1，3 接 2。 */
	TArray<int32> MakeChainParents()
	{
		return { -1, 0, 1, 2 };
	}

	/** 参考局部变换：骨轴 +X，骨长依次 10/20/30/40 厘米。 */
	TArray<FTransform> MakeChainReference()
	{
		TArray<FTransform> Result;
		Result.Add(FTransform(FQuat::Identity, FVector(0.0, 0.0, 0.0)));
		Result.Add(FTransform(FQuat::Identity, FVector(10.0, 0.0, 0.0)));
		Result.Add(FTransform(FQuat::Identity, FVector(0.0, 20.0, 0.0)));
		Result.Add(FTransform(FQuat::Identity, FVector(0.0, 0.0, 30.0)));
		return Result;
	}

	/** 一帧：每骨沿 +X 递增，间距与参考骨长一致（厘米 → 米）。 */
	FRefPosePoseContract::FDecodedFrame MakeStraightFrame(double Scale)
	{
		FRefPosePoseContract::FDecodedFrame Frame;
		Frame.PositionsMeters = {
			FVector3d(0.0, 0.0, 0.0),
			FVector3d(0.10 * Scale, 0.0, 0.0),
			FVector3d(0.30 * Scale, 0.0, 0.0),
			FVector3d(0.60 * Scale, 0.0, 0.0),
		};
		Frame.Rotations.Init(FQuat4d::Identity, TestBoneCount);
		return Frame;
	}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FRefPoseDecode6DRoundTripTest,
	"MotionWeaver.RefPose.Contract.Decode6DRoundTrip",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRefPoseDecode6DRoundTripTest::RunTest(const FString& Parameters)
{
	// 明确验证 manifest 的 row-major 交错语义：
	//   values[0]=m00, [1]=m01, [2]=m10, [3]=m11, [4]=m20, [5]=m21
	// 单位旋转应解出单位四元数，而不是「看起来转了 90 度」。
	const float Identity6D[6] = { 1.0f, 0.0f, 0.0f, 1.0f, 0.0f, 0.0f };
	const FQuat4d Identity = FRefPosePoseContract::Decode6D(
		MakeArrayView(Identity6D, 6));
	AddInfo(FString::Printf(TEXT("Identity decoded = (%f, %f, %f, %f)"),
		Identity.X, Identity.Y, Identity.Z, Identity.W));
	TestTrue(TEXT("单位 6D 解出单位四元数"), Identity.Equals(FQuat4d::Identity, 1e-6));

	// 绕 Z 轴 30 度：col0 = (cos, sin, 0)，col1 = (-sin, cos, 0)。
	const double Angle = FMath::DegreesToRadians(30.0);
	const double C = FMath::Cos(Angle);
	const double S = FMath::Sin(Angle);
	const float Rotated6D[6] = {
		static_cast<float>(C), static_cast<float>(-S),
		static_cast<float>(S), static_cast<float>(C),
		0.0f, 0.0f,
	};
	const FQuat4d Decoded = FRefPosePoseContract::Decode6D(
		MakeArrayView(Rotated6D, 6));

	// 四元数有 ±q 双覆盖，直接比分量会因符号选择误判；
	// 用「把 X 轴转过去」的解析结果判定，语义等价且稳定。
	// 绕 Z 转 θ：X 轴 → (cosθ, sinθ, 0)。
	const FVector3d Rotated = Decoded.RotateVector(FVector3d(1.0, 0.0, 0.0));
	const FVector3d ExpectedRotation(C, S, 0.0);
	TestTrue(TEXT("绕 Z 解出正确旋转"), Rotated.Equals(ExpectedRotation, 1e-6));

	// 编码必须与解码互逆；这一条直接抓「行列互换」类错误。
	float Encoded[6];
	FRefPosePoseContract::Encode6D(Decoded, Encoded);
	for (int32 Index = 0; Index < 6; ++Index)
	{
		TestEqual(
			FString::Printf(TEXT("6D[%d] 往返"), Index),
			Encoded[Index], Rotated6D[Index]);
	}
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FRefPoseRealPoseGroupGenerationTest,
	"MotionWeaver.RefPose.Input.RealPoseGroupGeneration",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRefPoseRealPoseGroupGenerationTest::RunTest(const FString& Parameters)
{
	// 用真实模型包 + SKM_Manny 的参考姿态构造一个最小姿态组，
	// 走完整的「关键帧 → 四输入 → NNE → 解码 → 解算 → 重定向」路径。
	// 这段资产是运行时构造的，不依赖 UEFN 网格在 NullRHI 下能否加载。
	const FString PackageDir =
		FPlatformMisc::GetEnvironmentVariable(TEXT("MW_REFPOSE_UE_PACKAGE"));
	if (PackageDir.IsEmpty())
	{
		AddInfo(TEXT("未设置 MW_REFPOSE_UE_PACKAGE，跳过真实姿态组生成检查。"));
		return true;
	}

	USkeletalMesh* Mesh = Cast<USkeletalMesh>(FSoftObjectPath(
		TEXT("/Game/Characters/UE5_Mannequins/Meshes/SKM_Manny")).TryLoad());
	if (Mesh == nullptr || Mesh->GetSkeleton() == nullptr)
	{
		AddInfo(TEXT("SKM_Manny 不可用，跳过真实姿态组生成检查。"));
		return true;
	}
	const FReferenceSkeleton& Reference = Mesh->GetSkeleton()->GetReferenceSkeleton();
	const TArray<FTransform>& ReferenceLocal = Reference.GetRefBonePose();

	FString Error;
	TStrongObjectPtr<URefPoseModelAsset> Model(
		URefPoseModelAsset::CreateFromOnnxFile(
			PackageDir / TEXT("model.onnx"), Error));
	if (!TestTrue(TEXT("创建模型资产"), Model.IsValid()))
	{
		AddError(Error);
		return true;
	}
	if (!TestTrue(TEXT("读取模型契约"),
		Model->LoadContractFromPackage(PackageDir, Error)))
	{
		AddError(Error);
		return true;
	}
	TestTrue(TEXT("模型契约可用"), Model->HasUsableContract());
	TestTrue(
		TEXT("帧率来自 manifest timing（120.0048…，不是默认 120）"),
		FMath::IsNearlyEqual(Model->FrameRate, 120.00480019200768, 1e-9));
	TestEqual(TEXT("历史窗口来自 manifest"), Model->HistoryFrames, 24);
	TestEqual(TEXT("未来窗口来自 manifest"), Model->FutureFrames, 24);

	const int32 UpperArm = Reference.FindBoneIndex(TEXT("upperarm_l"));
	if (UpperArm == INDEX_NONE)
	{
		AddInfo(TEXT("SKM_Manny 没有 upperarm_l，跳过真实姿态组生成检查。"));
		return true;
	}

	auto MakePose = [&Mesh, &Reference, &ReferenceLocal, UpperArm](double ArmDegrees)
	{
		TStrongObjectPtr<UPoseProxyAsset> Pose(NewObject<UPoseProxyAsset>());
		FPoseFrameSnapshot& Snapshot = Pose->Snapshot;
		Snapshot.SkeletonName = Mesh->GetSkeleton()->GetFName();
		Snapshot.BoneNames.Reserve(Reference.GetNum());
		for (int32 Index = 0; Index < Reference.GetNum(); ++Index)
		{
			Snapshot.BoneNames.Add(Reference.GetBoneName(Index));
		}
		Snapshot.LocalTransforms = ReferenceLocal;

		// 只改一根上臂：关键帧姿态不同，模型输入与输出都应随之变化。
		FTransform& Arm = Snapshot.LocalTransforms[UpperArm];
		Arm.SetRotation(Arm.GetRotation() * FQuat(
			FVector(0.0, 0.0, 1.0), FMath::DegreesToRadians(ArmDegrees)));

		Pose->Skeleton = const_cast<USkeleton*>(Mesh->GetSkeleton());
		Pose->PreviewMesh = Mesh;
		return MoveTemp(Pose);
	};

	TStrongObjectPtr<UPoseProxyAsset> PoseA = MakePose(-35.0);
	TStrongObjectPtr<UPoseProxyAsset> PoseB = MakePose(35.0);
	TStrongObjectPtr<UPoseGroupProxyAsset> Group(NewObject<UPoseGroupProxyAsset>());

	FPoseKey KeyA;
	KeyA.PoseAsset = PoseA.Get();
	KeyA.Frame = 0;
	KeyA.LocalLocation = FVector(0.0, 0.0, 0.0);
	KeyA.SetHeadingDegrees(0.0);

	FPoseKey KeyB;
	KeyB.PoseAsset = PoseB.Get();
	KeyB.Frame = 48;
	KeyB.LocalLocation = FVector(100.0, 0.0, 0.0);
	KeyB.SetHeadingDegrees(45.0);

	Group->Keys.Add(KeyA);
	Group->Keys.Add(KeyB);
	Group->Settings.FrameRate = 120.0;
	Group->Solve();
	if (!Group->GetDiagnostics().IsValid())
	{
		for (const FString& Diagnostic : Group->GetDiagnostics().Errors)
		{
			AddError(Diagnostic);
		}
		TestTrue(TEXT("最小姿态组求解成功"), Group->GetDiagnostics().IsValid());
		return true;
	}

	FRefPoseGroupGenerationResult Result;
	if (!TestTrue(TEXT("真实姿态组生成成功"),
		FRefPoseGroupGenerator::Generate(*Group, *Model, Result, Error)))
	{
		AddError(Error);
		return true;
	}

	const int32 ExpectedFrames =
		Group->GetSamples().Num() - Model->HistoryFrames;
	const int32 ExpectedWindows =
		(ExpectedFrames + Model->FutureFrames - 1) / Model->FutureFrames;

	// 多窗口：求解样本去掉 24 帧历史后逐窗口产出，末窗按实际帧数截断。
	TestEqual(
		TEXT("生成帧数"),
		Result.Frames.Num(),
		ExpectedFrames);
	TestEqual(TEXT("UE 骨架骨数"), Result.SkeletonBoneCount, Reference.GetNum());
	TestEqual(TEXT("模型骨数"), Result.ModelBoneCount, Model->BoneCount);
	TestEqual(TEXT("未映射骨数"), Result.UnmappedBoneCount, 16);
	TestEqual(TEXT("窗口数"), Result.Metrics.WindowCount, ExpectedWindows);
	TestEqual(TEXT("诊断帧数"), Result.Metrics.FrameCount, Result.Frames.Num());
	TestTrue(TEXT("确实执行了 NNE 推理"), Result.Metrics.TotalInferenceMs > 0.0);

	double MaxMotionCm = 0.0;
	for (int32 FrameIndex = 0; FrameIndex < Result.Frames.Num(); ++FrameIndex)
	{
		const TArray<FTransform>& Frame = Result.Frames[FrameIndex];
		TestEqual(
			FString::Printf(TEXT("帧 %d 骨数"), FrameIndex),
			Frame.Num(), Reference.GetNum());
		for (int32 Bone = 0; Bone < Frame.Num(); ++Bone)
		{
			if (Frame[Bone].GetTranslation().ContainsNaN()
				|| Frame[Bone].GetRotation().ContainsNaN()
				|| Frame[Bone].GetScale3D().ContainsNaN())
			{
				AddError(FString::Printf(
					TEXT("帧 %d 骨 %d 含 NaN。"), FrameIndex, Bone));
				return true;
			}
		}
		if (FrameIndex > 0)
		{
			for (int32 Bone = 0; Bone < Frame.Num(); ++Bone)
			{
				MaxMotionCm = FMath::Max(
					MaxMotionCm,
					(Frame[Bone].GetTranslation()
						- Result.Frames[0][Bone].GetTranslation()).Size());
			}
		}
	}

	// 两个关键帧的姿态与 Root 朝向都不同；输出必须真的在动，
	// 不能退化成复制参考姿态的静态序列。
	TestTrue(TEXT("模型输出不是静态参考姿态"), MaxMotionCm > 0.1);
	const double RootYawDelta =
		FMath::RadiansToDegrees(
			Result.Frames[0][0].GetRotation().AngularDistance(
				Result.Frames.Last()[0].GetRotation()));
	TestTrue(TEXT("Root 朝向跟随姿态组轨迹"), RootYawDelta > 1.0);
	return true;
}

namespace
{
	TStrongObjectPtr<UPoseProxyAsset> MakeTestPose(
		USkeletalMesh* Mesh,
		const FReferenceSkeleton& Reference,
		const TArray<FTransform>& ReferenceLocal,
		int32 UpperArm,
		double ArmDegrees)
	{
		TStrongObjectPtr<UPoseProxyAsset> Pose(NewObject<UPoseProxyAsset>());
		FPoseFrameSnapshot& Snapshot = Pose->Snapshot;
		Snapshot.SkeletonName = Mesh->GetSkeleton()->GetFName();
		Snapshot.BoneNames.Reserve(Reference.GetNum());
		for (int32 Index = 0; Index < Reference.GetNum(); ++Index)
		{
			Snapshot.BoneNames.Add(Reference.GetBoneName(Index));
		}
		Snapshot.LocalTransforms = ReferenceLocal;

		FTransform& Arm = Snapshot.LocalTransforms[UpperArm];
		Arm.SetRotation(Arm.GetRotation() * FQuat(
			FVector(0.0, 0.0, 1.0), FMath::DegreesToRadians(ArmDegrees)));

		Pose->Skeleton = const_cast<USkeleton*>(Mesh->GetSkeleton());
		Pose->PreviewMesh = Mesh;
		return Pose;
	}

	void AddModelBone(
		URefPoseModelAsset& Model,
		const TCHAR* Name,
		int32 Parent,
		const FVector3d& LocalPositionMeters,
		const FQuat4d& LocalRotation)
	{
		FRefPoseBoneContract& Bone = Model.Bones.AddDefaulted_GetRef();
		Bone.Name = FName(Name);
		Bone.Parent = Parent;
		Bone.LocalPositionMeters = LocalPositionMeters;
		Bone.LocalRotation = LocalRotation;
		Model.BoneCount = Model.Bones.Num();
	}

	FQuat4d UeYaw(double Degrees)
	{
		return FQuat4d(
			FVector3d(0.0, 0.0, 1.0), FMath::DegreesToRadians(Degrees));
	}

	FRefPoseWindowBuilder::FRootSample MakeTrackSample(
		const FVector3d& PositionMeters,
		double HeadingDegrees)
	{
		FRefPoseWindowBuilder::FRootSample Sample;
		Sample.PositionMeters = PositionMeters;
		Sample.Rotation = FRefPosePoseContract::ToMotionRotation(
			UeYaw(HeadingDegrees));
		return Sample;
	}

	FQuat4d DecodePoseBone(
		const FRefPoseGenerator::FPoseInput& Pose,
		int32 BoneIndex)
	{
		return FRefPosePoseContract::Decode6D(
			MakeArrayView(Pose.Rotations6D.GetData() + BoneIndex * 6, 6));
	}

	bool TestRotationEquals(
		FAutomationTestBase& Test,
		const TCHAR* What,
		const FQuat4d& Actual,
		const FQuat4d& Expected)
	{
		// 四元数有 ±q 双覆盖，直接比分量会误判；比较旋转结果。
		const FVector3d Probe(1.0, 2.0, 3.0);
		const bool bEqual = Actual.RotateVector(Probe).Equals(
			Expected.RotateVector(Probe), 1e-7);
		Test.TestTrue(What, bEqual);
		return bEqual;
	}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FRefPoseKeyframePoseInputTest,
	"MotionWeaver.RefPose.Input.SkeletonPoseConvertsToRootLocal",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRefPoseKeyframePoseInputTest::RunTest(const FString& Parameters)
{
	// 合成一套「模型 5 骨 / UE 4 骨」：模型多出的 HeadEnd 没有 UE 对应。
	TStrongObjectPtr<URefPoseModelAsset> Model(NewObject<URefPoseModelAsset>());
	AddModelBone(*Model, TEXT("Root"), -1, FVector3d::ZeroVector, FQuat4d::Identity);
	AddModelBone(*Model, TEXT("Hips"), 0, FVector3d(0.0, 1.0, 0.0), FQuat4d::Identity);
	AddModelBone(*Model, TEXT("Spine1"), 1, FVector3d(0.0, 0.1, 0.0), FQuat4d::Identity);
	AddModelBone(*Model, TEXT("Head"), 2, FVector3d(0.0, 0.6, 0.0), FQuat4d::Identity);
	AddModelBone(
		*Model, TEXT("HeadEnd"), 3, FVector3d(0.2, 0.0, 0.0), FQuat4d::Identity);

	const TArray<FName> SkeletonNames = {
		TEXT("root"), TEXT("pelvis"), TEXT("spine_01"), TEXT("head") };
	const TArray<int32> SkeletonParents = { -1, 0, 1, 2 };
	TMap<FName, FName> Names;
	Names.Add(TEXT("Root"), TEXT("root"));
	Names.Add(TEXT("Hips"), TEXT("pelvis"));
	Names.Add(TEXT("Spine1"), TEXT("spine_01"));
	Names.Add(TEXT("Head"), TEXT("head"));

	FRefPoseSkeletonMap Map;
	TArray<FName> Unmapped;
	FString Error;
	const TArray<FName> ModelNames = {
		TEXT("Root"), TEXT("Hips"), TEXT("Spine1"), TEXT("Head"), TEXT("HeadEnd") };
	if (!TestTrue(TEXT("映射建立成功"), FRefPoseSkeletonMap::Build(
		ModelNames, SkeletonNames, Names, Map, Unmapped, Error)))
	{
		AddError(Error);
		return true;
	}
	TestEqual(TEXT("HeadEnd 未映射"), Unmapped.Num(), 1);

	TArray<FTransform> SkeletonReference;
	SkeletonReference.Init(FTransform::Identity, SkeletonNames.Num());
	TArray<FTransform> SkeletonLocal;
	SkeletonLocal.Add(FTransform(UeYaw(45.0), FVector(0.0, 0.0, 50.0)));
	SkeletonLocal.Add(FTransform(UeYaw(30.0), FVector(0.0, 0.0, 100.0)));
	SkeletonLocal.Add(FTransform(UeYaw(10.0), FVector(0.0, 0.0, 50.0)));
	SkeletonLocal.Add(FTransform(UeYaw(5.0), FVector(0.0, 0.0, 60.0)));

	const FRefPoseWindowBuilder::FRootSample Track =
		MakeTrackSample(FVector3d(1.0, 0.0, 2.0), 90.0);

	FRefPoseGenerator::FPoseInput Pose;
	if (!TestTrue(TEXT("关键帧姿态转换成功"),
		FRefPoseGenerator::SkeletonPoseToPoseInput(
			*Model, Map, SkeletonLocal, SkeletonParents, Track, Pose, Error)))
	{
		AddError(Error);
		return true;
	}

	// 由 UE 局部变换独立重建组件空间；这里不复用被测函数。
	TArray<FTransform> Global;
	Global.SetNum(SkeletonLocal.Num());
	for (int32 Index = 0; Index < SkeletonLocal.Num(); ++Index)
	{
		const int32 Parent = SkeletonParents[Index];
		Global[Index] = Parent < 0
			? SkeletonLocal[Index]
			: Global[Parent] * SkeletonLocal[Index];
	}
	const FQuat4d RootRotationInv =
		FQuat4d(Global[0].GetRotation()).GetNormalized().Inverse();
	const FQuat4d TrackRotation = Track.Rotation.GetNormalized();

	// Root 必须满足 manifest 的独立判据：world = R(k)·p_root + k_pos = 0。
	const FVector3d RootWorld =
		TrackRotation.RotateVector(Pose.PositionsMeters[0]) + Track.PositionMeters;
	TestTrue(TEXT("Root 局部位置满足 world=0"),
		RootWorld.Equals(FVector3d::ZeroVector, 1e-9));

	for (int32 ModelIndex = 1; ModelIndex <= 3; ++ModelIndex)
	{
		const int32 SkeletonIndex = Map.ModelToSkeleton[ModelIndex];
		const FVector ExpectedMotion = FRefPosePoseContract::ToMotionPosition(
			RootRotationInv.RotateVector(
				Global[SkeletonIndex].GetTranslation() - Global[0].GetTranslation()));
		const FVector3d ActualWorld =
			TrackRotation.RotateVector(Pose.PositionsMeters[ModelIndex])
			+ Track.PositionMeters;
		TestTrue(
			FString::Printf(TEXT("骨 %d 的 Root 局部位置"), ModelIndex),
			ActualWorld.Equals(FVector3d(ExpectedMotion), 1e-9));

		const FQuat4d ExpectedWorldRotation =
			FRefPosePoseContract::ToMotionRotation((
				RootRotationInv
				* FQuat4d(Global[SkeletonIndex].GetRotation()).GetNormalized()
			).GetNormalized());
		const FQuat4d ActualWorldRotation =
			(TrackRotation * DecodePoseBone(Pose, ModelIndex)).GetNormalized();
		TestRotationEquals(
			*this,
			*FString::Printf(TEXT("骨 %d 的 Root 局部旋转"), ModelIndex),
			ActualWorldRotation,
			ExpectedWorldRotation);
	}

	// 未映射骨必须相对最近的已映射祖先按模型参考偏移落下。
	const FVector3d HeadToEnd =
		Pose.PositionsMeters[4] - Pose.PositionsMeters[3];
	const FQuat4d HeadRotation = DecodePoseBone(Pose, 3);
	const FVector3d HeadLocalDirection =
		HeadRotation.Inverse().RotateVector(HeadToEnd);
	TestTrue(TEXT("HeadEnd 相对 Head 的模型参考偏移"),
		HeadLocalDirection.Equals(FVector3d(0.2, 0.0, 0.0), 1e-9));
	TestRotationEquals(
		*this,
		TEXT("HeadEnd 旋转跟随 Head 参考"),
		DecodePoseBone(Pose, 4),
		HeadRotation);

	// 快照根骨自己的平移/旋转必须被丢弃：root motion 的唯一来源是姿态组轨迹。
	TArray<FTransform> NoRootLocal = SkeletonLocal;
	NoRootLocal[0] = FTransform::Identity;
	FRefPoseGenerator::FPoseInput NoRootPose;
	if (!TestTrue(TEXT("无根变换版本转换成功"),
		FRefPoseGenerator::SkeletonPoseToPoseInput(
			*Model, Map, NoRootLocal, SkeletonParents, Track, NoRootPose, Error)))
	{
		AddError(Error);
		return true;
	}
	for (int32 ModelIndex = 0; ModelIndex < Model->BoneCount; ++ModelIndex)
	{
		TestTrue(
			FString::Printf(TEXT("骨 %d 不受快照根变换影响"), ModelIndex),
			Pose.PositionsMeters[ModelIndex].Equals(
				NoRootPose.PositionsMeters[ModelIndex], 1e-9));
		TestRotationEquals(
			*this,
			*FString::Printf(TEXT("骨 %d 旋转不受快照根变换影响"), ModelIndex),
			DecodePoseBone(Pose, ModelIndex),
			DecodePoseBone(NoRootPose, ModelIndex));
	}
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FRefPoseSnapshotResolveTest,
	"MotionWeaver.RefPose.Input.SnapshotResolvesByName",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRefPoseSnapshotResolveTest::RunTest(const FString& Parameters)
{
	const TArray<FName> SkeletonNames = {
		TEXT("root"), TEXT("pelvis"), TEXT("spine_01") };
	TArray<FTransform> Reference;
	Reference.Add(FTransform(FQuat::Identity, FVector(0.0, 0.0, 1.0)));
	Reference.Add(FTransform(FQuat::Identity, FVector(0.0, 0.0, 2.0)));
	Reference.Add(FTransform(FQuat::Identity, FVector(0.0, 0.0, 3.0)));

	FPoseFrameSnapshot Snapshot;
	Snapshot.BoneNames = { TEXT("pelvis"), TEXT("root") };
	Snapshot.LocalTransforms = {
		FTransform(UeYaw(30.0), FVector(0.0, 0.0, 100.0)),
		FTransform(UeYaw(45.0), FVector(0.0, 0.0, 50.0)),
	};

	TArray<FTransform> OutLocal;
	FString Error;
	if (!TestTrue(TEXT("按骨名解析成功"),
		FRefPoseGenerator::SnapshotToSkeletonLocal(
			Snapshot, SkeletonNames, Reference, OutLocal, Error)))
	{
		AddError(Error);
		return true;
	}
	TestEqual(TEXT("输出骨数"), OutLocal.Num(), SkeletonNames.Num());
	TestTrue(TEXT("root 槽位取的是快照第二项，不是第一项"),
		OutLocal[0].GetTranslation().Equals(FVector(0.0, 0.0, 50.0), 1e-9));
	TestTrue(TEXT("pelvis 槽位取的是快照第一项"),
		OutLocal[1].GetTranslation().Equals(FVector(0.0, 0.0, 100.0), 1e-9));
	TestRotationEquals(
		*this, TEXT("pelvis 旋转按名字对上"),
		FQuat4d(OutLocal[1].GetRotation()), UeYaw(30.0));
	// 快照没有的骨保持 UE 参考姿态。
	TestTrue(TEXT("缺失骨保持参考"),
		OutLocal[2].GetTranslation().Equals(Reference[2].GetTranslation(), 1e-9));

	// 快照里出现参考骨架没有的骨名：必须报错，不能静默丢。
	FPoseFrameSnapshot Unknown = Snapshot;
	Unknown.BoneNames.Add(TEXT("does_not_exist"));
	Unknown.LocalTransforms.Add(FTransform::Identity);
	TestFalse(TEXT("未知骨被拒绝"),
		FRefPoseGenerator::SnapshotToSkeletonLocal(
			Unknown, SkeletonNames, Reference, OutLocal, Error));
	TestFalse(TEXT("未知骨返回可诊断错误"), Error.IsEmpty());

	// 骨名与变换数量不一致：拒绝。
	FPoseFrameSnapshot Inconsistent = Snapshot;
	Inconsistent.LocalTransforms.Pop();
	TestFalse(TEXT("不一致快照被拒绝"),
		FRefPoseGenerator::SnapshotToSkeletonLocal(
			Inconsistent, SkeletonNames, Reference, OutLocal, Error));
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FRefPoseBlendSkeletonLocalTest,
	"MotionWeaver.RefPose.Input.BlendSkeletonLocal",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRefPoseBlendSkeletonLocalTest::RunTest(const FString& Parameters)
{
	TArray<FTransform> A;
	A.Add(FTransform(FQuat::Identity, FVector::ZeroVector));
	TArray<FTransform> B;
	B.Add(FTransform(UeYaw(90.0), FVector(10.0, 0.0, 0.0)));

	TArray<FTransform> Out;
	FString Error;
	if (!TestTrue(TEXT("插值成功"),
		FRefPoseGenerator::BlendSkeletonLocal(A, B, 0.5, Out, Error)))
	{
		AddError(Error);
		return true;
	}
	TestEqual(TEXT("插值骨数"), Out.Num(), 1);
	TestTrue(TEXT("位置线性插值"),
		Out[0].GetTranslation().Equals(FVector(5.0, 0.0, 0.0), 1e-9));

	const FVector Rotated = Out[0].GetRotation().RotateVector(FVector::ForwardVector);
	const FVector Expected(
		FMath::Cos(FMath::DegreesToRadians(45.0)),
		FMath::Sin(FMath::DegreesToRadians(45.0)),
		0.0);
	TestTrue(TEXT("旋转按最短弧插值"), Rotated.Equals(Expected, 1e-7));

	TestFalse(TEXT("骨数不一致被拒绝"),
		FRefPoseGenerator::BlendSkeletonLocal(
			A, TArray<FTransform>(), 0.5, Out, Error));
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FRefPoseSolverReferenceTest,
	"MotionWeaver.RefPose.Input.SolverReferenceUsesUeRest",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRefPoseSolverReferenceTest::RunTest(const FString& Parameters)
{
	TStrongObjectPtr<URefPoseModelAsset> Model(NewObject<URefPoseModelAsset>());
	AddModelBone(*Model, TEXT("Root"), -1, FVector3d::ZeroVector, FQuat4d::Identity);
	AddModelBone(*Model, TEXT("Hips"), 0, FVector3d(0.0, 1.0, 0.0), FQuat4d::Identity);
	AddModelBone(*Model, TEXT("Chest"), 1, FVector3d(0.0, 0.3, 0.0), FQuat4d::Identity);
	AddModelBone(*Model, TEXT("Neck1"), 2, FVector3d(0.0, 0.2, 0.0), FQuat4d::Identity);

	const TArray<FName> SkeletonNames = {
		TEXT("root"), TEXT("pelvis"),
		TEXT("spine_01"), TEXT("spine_02"), TEXT("spine_03"),
		TEXT("spine_04"), TEXT("spine_05"), TEXT("neck_01") };
	const TArray<int32> SkeletonParents = { -1, 0, 1, 2, 3, 4, 5, 6 };

	TMap<FName, FName> Names;
	Names.Add(TEXT("Root"), TEXT("root"));
	Names.Add(TEXT("Hips"), TEXT("pelvis"));
	Names.Add(TEXT("Chest"), TEXT("spine_03"));
	Names.Add(TEXT("Neck1"), TEXT("neck_01"));

	FRefPoseSkeletonMap Map;
	TArray<FName> Unmapped;
	FString Error;
	const TArray<FName> ModelNames = {
		TEXT("Root"), TEXT("Hips"), TEXT("Chest"), TEXT("Neck1") };
	if (!TestTrue(TEXT("映射建立成功"), FRefPoseSkeletonMap::Build(
		ModelNames, SkeletonNames, Names, Map, Unmapped, Error)))
	{
		AddError(Error);
		return true;
	}

	TArray<FTransform> Reference;
	Reference.Add(FTransform(FQuat::Identity, FVector(0.0, 0.0, 100.0)));
	Reference.Add(FTransform(UeYaw(10.0), FVector(0.0, 0.0, 100.0)));
	Reference.Add(FTransform(UeYaw(20.0), FVector(0.0, 0.0, 100.0)));
	Reference.Add(FTransform(UeYaw(30.0), FVector(0.0, 0.0, 100.0)));
	Reference.Add(FTransform(UeYaw(40.0), FVector(0.0, 0.0, 100.0)));
	Reference.Add(FTransform(UeYaw(50.0), FVector(0.0, 0.0, 100.0)));
	Reference.Add(FTransform(UeYaw(60.0), FVector(0.0, 0.0, 100.0)));
	Reference.Add(FTransform(UeYaw(70.0), FVector(0.0, 0.0, 100.0)));

	TArray<FTransform> SolverReference;
	if (!TestTrue(TEXT("求解器参考构造成功"),
		FRefPoseGenerator::BuildSolverReference(
			*Model, Map, Reference, SkeletonParents, SolverReference, Error)))
	{
		AddError(Error);
		return true;
	}
	TestEqual(TEXT("参考变换数"), SolverReference.Num(), Model->BoneCount);

	// 长度仍取模型契约：最终骨长由 Retarget 换成 UE 的。
	TestTrue(TEXT("Hips 长度取模型契约"),
		FMath::IsNearlyEqual(SolverReference[1].GetTranslation().Size(), 100.0, 1e-9));
	TestTrue(TEXT("Chest 长度取模型契约"),
		FMath::IsNearlyEqual(SolverReference[2].GetTranslation().Size(), 30.0, 1e-9));
	TestTrue(TEXT("Neck1 长度取模型契约"),
		FMath::IsNearlyEqual(SolverReference[3].GetTranslation().Size(), 20.0, 1e-9));

	// Hips 相对 root；Chest 相对 pelvis，需要穿过 spine_01..03；
	// Neck1 相对 Chest，需要穿过 spine_04/spine_05。
	TestRotationEquals(*this, TEXT("Hips 相对 UE 参考"),
		FQuat4d(SolverReference[1].GetRotation()), UeYaw(10.0));
	TestRotationEquals(*this, TEXT("Chest 相对 UE 参考"),
		FQuat4d(SolverReference[2].GetRotation()),
		(UeYaw(20.0) * UeYaw(30.0) * UeYaw(40.0)).GetNormalized());
	TestRotationEquals(*this, TEXT("Neck1 穿过未映射中间骨"),
		FQuat4d(SolverReference[3].GetRotation()),
		(UeYaw(50.0) * UeYaw(60.0) * UeYaw(70.0)).GetNormalized());
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FRefPoseAxisRoundTripTest,
	"MotionWeaver.RefPose.Contract.AxisRoundTrip",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRefPoseAxisRoundTripTest::RunTest(const FString& Parameters)
{
	// 位置：米制 Y-up → UE 厘米 → 回米制 Y-up。
	const FVector3d Samples[] = {
		FVector3d(0.0, 1.0, 0.0),
		FVector3d(0.5, -0.25, 1.5),
		FVector3d(-2.0, 0.125, -0.75),
	};
	for (const FVector3d& Sample : Samples)
	{
		const FVector Ue = FRefPosePoseContract::ToUePosition(Sample);
		// 明确逐轴期望：ue = (z, -x, y) * 100。
		TestEqual(TEXT("ue.x"), Ue.X, Sample.Z * 100.0);
		TestEqual(TEXT("ue.y"), Ue.Y, -Sample.X * 100.0);
		TestEqual(TEXT("ue.z"), Ue.Z, Sample.Y * 100.0);

		const FVector3d Back = FRefPosePoseContract::ToMotionPosition(Ue);
		TestTrue(TEXT("位置往返"), Back.Equals(Sample, 1e-9));
	}

	// 旋转：基变换两次应回到原值。
	const FQuat4d Rotation(FVector(0.0, 0.0, 1.0), FMath::DegreesToRadians(37.0));
	const FQuat4d UeRotation = FRefPosePoseContract::ToUeRotation(Rotation);
	const FQuat4d BackRotation = FRefPosePoseContract::ToMotionRotation(UeRotation);
	TestTrue(TEXT("旋转往返"), BackRotation.Equals(Rotation, 1e-9));
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FRefPoseRealPoseGroupSoftTrajectoryTest,
	"MotionWeaver.RefPose.Input.RealPoseGroupSoftTrajectory",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRefPoseRealPoseGroupSoftTrajectoryTest::RunTest(const FString& Parameters)
{
	// 复现「关键帧跨度短、但求解器通过时长膨胀产出了足够长的轨迹」：
	// 超速是创作质量告警，不应把预览入口整体判死。
	const FString PackageDir =
		FPlatformMisc::GetEnvironmentVariable(TEXT("MW_REFPOSE_UE_PACKAGE"));
	if (PackageDir.IsEmpty())
	{
		AddInfo(TEXT("未设置 MW_REFPOSE_UE_PACKAGE，跳过超速轨迹生成检查。"));
		return true;
	}

	USkeletalMesh* Mesh = Cast<USkeletalMesh>(FSoftObjectPath(
		TEXT("/Game/Characters/UE5_Mannequins/Meshes/SKM_Manny")).TryLoad());
	if (Mesh == nullptr || Mesh->GetSkeleton() == nullptr)
	{
		AddInfo(TEXT("SKM_Manny 不可用，跳过超速轨迹生成检查。"));
		return true;
	}
	const FReferenceSkeleton& Reference = Mesh->GetSkeleton()->GetReferenceSkeleton();
	const TArray<FTransform>& ReferenceLocal = Reference.GetRefBonePose();
	const int32 UpperArm = Reference.FindBoneIndex(TEXT("upperarm_l"));
	if (UpperArm == INDEX_NONE)
	{
		AddInfo(TEXT("SKM_Manny 没有 upperarm_l，跳过超速轨迹生成检查。"));
		return true;
	}

	FString Error;
	TStrongObjectPtr<URefPoseModelAsset> Model(
		URefPoseModelAsset::CreateFromOnnxFile(
			PackageDir / TEXT("model.onnx"), Error));
	if (!TestTrue(TEXT("创建模型资产"), Model.IsValid()))
	{
		AddError(Error);
		return true;
	}
	if (!TestTrue(TEXT("读取模型契约"),
		Model->LoadContractFromPackage(PackageDir, Error)))
	{
		AddError(Error);
		return true;
	}

	TStrongObjectPtr<UPoseProxyAsset> PoseA =
		MakeTestPose(Mesh, Reference, ReferenceLocal, UpperArm, -35.0);
	TStrongObjectPtr<UPoseProxyAsset> PoseB =
		MakeTestPose(Mesh, Reference, ReferenceLocal, UpperArm, 35.0);
	TStrongObjectPtr<UPoseGroupProxyAsset> Group(NewObject<UPoseGroupProxyAsset>());

	FPoseKey KeyA;
	KeyA.PoseAsset = PoseA.Get();
	KeyA.Frame = 0;
	KeyA.LocalLocation = FVector(0.0, 0.0, 0.0);
	KeyA.SetHeadingDegrees(0.0);

	FPoseKey KeyB;
	KeyB.PoseAsset = PoseB.Get();
	KeyB.Frame = 12;
	KeyB.LocalLocation = FVector(400.0, 0.0, 0.0);
	KeyB.SetHeadingDegrees(0.0);

	FPoseKey KeyC;
	KeyC.PoseAsset = PoseA.Get();
	KeyC.Frame = 36;
	KeyC.LocalLocation = FVector(800.0, 0.0, 0.0);
	KeyC.SetHeadingDegrees(0.0);

	Group->Keys.Add(KeyA);
	Group->Keys.Add(KeyB);
	Group->Keys.Add(KeyC);
	Group->Settings.FrameRate = 120.0;
	Group->Solve();

	TestTrue(TEXT("求解产生了轨迹样本"), Group->GetSamples().Num() > 0);
	TestFalse(TEXT("求解器确实报告了超速"), Group->GetDiagnostics().IsValid());

	const int32 WindowFrames = Model->HistoryFrames + Model->FutureFrames;
	TestTrue(
		FString::Printf(
			TEXT("时长膨胀后的样本数 %d 足以覆盖窗口 %d"),
			Group->GetSamples().Num(), WindowFrames),
		Group->GetSamples().Num() >= WindowFrames);

	// 原始关键帧跨度只有 37 帧（0..36），短于 48 帧窗口。
	TestTrue(TEXT("原始关键帧跨度短于窗口"),
		Group->Keys.Last().Frame - Group->Keys[0].Frame + 1 < WindowFrames);

	FRefPoseGroupGenerationResult Result;
	if (!TestTrue(TEXT("超速轨迹仍可生成预览"),
		FRefPoseGroupGenerator::Generate(*Group, *Model, Result, Error)))
	{
		AddError(Error);
		return true;
	}

	const int32 ExpectedFrames =
		Group->GetSamples().Num() - Model->HistoryFrames;
	const int32 ExpectedWindows =
		(ExpectedFrames + Model->FutureFrames - 1) / Model->FutureFrames;
	TestEqual(
		TEXT("生成帧数"),
		Result.Frames.Num(),
		ExpectedFrames);
	TestEqual(TEXT("窗口数"), Result.Metrics.WindowCount, ExpectedWindows);
	TestTrue(TEXT("确实覆盖了多个窗口"), Result.Frames.Num() > Model->FutureFrames);
	TestTrue(TEXT("求解告警被带到生成结果"), Result.Warnings.Num() > 0);

	// 窗口接缝不能把 Root 原点重复叠加：相邻帧的根位移应保持连续。
	double MaxRootJumpCm = 0.0;
	for (int32 FrameIndex = 1; FrameIndex < Result.Frames.Num(); ++FrameIndex)
	{
		MaxRootJumpCm = FMath::Max(
			MaxRootJumpCm,
			static_cast<double>((Result.Frames[FrameIndex][0].GetTranslation()
				- Result.Frames[FrameIndex - 1][0].GetTranslation()).Size()));
	}
	AddInfo(FString::Printf(TEXT("Root 相邻帧最大跳变 = %.2f cm"), MaxRootJumpCm));
	TestTrue(TEXT("窗口接缝处 Root 不跳变"), MaxRootJumpCm < 100.0);
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FRefPoseDecodeWindowTest,
	"MotionWeaver.RefPose.Contract.DecodeWindow",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRefPoseDecodeWindowTest::RunTest(const FString& Parameters)
{
	// 单位统计量时，反归一化应等于原始值；再验证 NaN 与长度错误会被拒绝。
	const int32 BoneCount = TestBoneCount;
	const int32 Frames = 2;
	TArray<float> Mean, Std;
	Mean.Init(0.0f, BoneCount * 3);
	Std.Init(1.0f, BoneCount * 3);

	TArray<float> Raw;
	Raw.SetNumZeroed(Frames * BoneCount * 9);
	for (int32 Frame = 0; Frame < Frames; ++Frame)
	{
		for (int32 Bone = 0; Bone < BoneCount; ++Bone)
		{
			float* Base = Raw.GetData() + (Frame * BoneCount + Bone) * 9;
			Base[0] = static_cast<float>(Frame + Bone);
			Base[1] = 0.5f;
			Base[2] = -0.25f;
			// 位置占 0..2，旋转 6D 占 3..8。
			// 单位 6D（m00,m01,m10,m11,m20,m21）= (1,0,0,1,0,0)。
			Base[3] = 1.0f;
			Base[4] = 0.0f;
			Base[5] = 0.0f;
			Base[6] = 1.0f;
			Base[7] = 0.0f;
			Base[8] = 0.0f;
		}
	}

	TArray<FRefPosePoseContract::FDecodedFrame> Decoded;
	FString Error;
	TestTrue(TEXT("合法输出解码成功"), FRefPosePoseContract::DecodeWindow(
		Raw, BoneCount, Frames, Mean, Std, Decoded, Error));
	TestEqual(TEXT("帧数"), Decoded.Num(), Frames);
	TestEqual(TEXT("首帧首骨 X"), Decoded[0].PositionsMeters[0].X, 0.0);
	TestEqual(TEXT("次帧末骨 X"), Decoded[1].PositionsMeters[BoneCount - 1].X,
		static_cast<double>(1 + BoneCount - 1));
	TestTrue(TEXT("单位 6D 解出单位旋转"),
		Decoded[0].Rotations[0].Equals(FQuat4d::Identity, 1e-6));

	TestFalse(TEXT("长度错误被拒绝"), FRefPosePoseContract::DecodeWindow(
		MakeArrayView(Raw.GetData(), Raw.Num() - 1),
		BoneCount, Frames, Mean, Std, Decoded, Error));

	TArray<float> Poisoned = Raw;
	Poisoned[0] = std::numeric_limits<float>::quiet_NaN();
	TestFalse(TEXT("NaN 被拒绝"), FRefPosePoseContract::DecodeWindow(
		Poisoned, BoneCount, Frames, Mean, Std, Decoded, Error));
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FRefPoseSolverBoneLengthTest,
	"MotionWeaver.RefPose.Contract.SolverPreservesBoneLength",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRefPoseSolverBoneLengthTest::RunTest(const FString& Parameters)
{
	const TArray<int32> Parents = MakeChainParents();
	const TArray<FTransform> Reference = MakeChainReference();

	// 两个骨长不同的帧：无论模型给多长的偏移，输出骨长都必须等于参考骨长。
	TArray<FRefPosePoseContract::FDecodedFrame> Frames;
	Frames.Add(MakeStraightFrame(1.0));
	Frames.Add(MakeStraightFrame(2.5));

	TArray<TArray<FTransform>> Local;
	FRefPoseSkeletonSolver::FMetrics Metrics;
	FString Error;
	if (!TestTrue(TEXT("解算成功"), FRefPoseSkeletonSolver::Solve(
		Parents, Reference, Frames, Local, Metrics, Error)))
	{
		AddError(Error);
		return true;
	}

	TestEqual(TEXT("帧数"), Local.Num(), Frames.Num());
	TestEqual(TEXT("退化骨数"), Metrics.DegenerateBones, 0);
	TestEqual(TEXT("骨长偏差"), Metrics.MaxBoneLengthDeviationCm, 0.0);

	for (int32 FrameIndex = 0; FrameIndex < Local.Num(); ++FrameIndex)
	{
		for (int32 Bone = 1; Bone < TestBoneCount; ++Bone)
		{
			TestTrue(
				FString::Printf(TEXT("帧 %d 骨 %d 骨长"), FrameIndex, Bone),
				FMath::IsNearlyEqual(
					Local[FrameIndex][Bone].GetTranslation().Size(),
					Reference[Bone].GetTranslation().Size(),
					1e-9));
		}
	}
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FRefPoseSolverDirectionTest,
	"MotionWeaver.RefPose.Contract.SolverFollowsPositions",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRefPoseSolverDirectionTest::RunTest(const FString& Parameters)
{
	// 位置沿 +Y（米制）走，UE 里应对应 +Z 方向。
	const TArray<int32> Parents = MakeChainParents();
	const TArray<FTransform> Reference = MakeChainReference();
	TArray<FRefPosePoseContract::FDecodedFrame> Frames;

	FRefPosePoseContract::FDecodedFrame Frame;
	Frame.PositionsMeters = {
		FVector3d(0.0, 0.0, 0.0),
		FVector3d(0.0, 0.10, 0.0),
		FVector3d(0.0, 0.30, 0.0),
		FVector3d(0.0, 0.60, 0.0),
	};
	Frame.Rotations.Init(FQuat4d::Identity, TestBoneCount);
	Frames.Add(Frame);

	TArray<TArray<FTransform>> Local;
	FRefPoseSkeletonSolver::FMetrics Metrics;
	FString Error;
	if (!TestTrue(TEXT("解算成功"), FRefPoseSkeletonSolver::Solve(
		Parents, Reference, Frames, Local, Metrics, Error)))
	{
		AddError(Error);
		return true;
	}

	const FVector FirstTranslation = Local[0][1].GetTranslation().GetSafeNormal();
	TestTrue(TEXT("骨 1 方向为 UE +Z"),
		FirstTranslation.Equals(FVector(0.0, 0.0, 1.0), 1e-6));

	// 自检闭环：复算的骨架空间位置，其方向必须与输入一致。
	TArray<TArray<FVector>> Rebuilt;
	TestTrue(TEXT("复算成功"), FRefPoseSkeletonSolver::RebuildSkeletonPositions(
		Parents, Local, Rebuilt, Error));
	const FVector RebuiltDirection = (Rebuilt[0][1] - Rebuilt[0][0]).GetSafeNormal();
	TestTrue(TEXT("复算方向与输入一致"),
		RebuiltDirection.Equals(FVector(0.0, 0.0, 1.0), 1e-6));
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FRefPoseSolverComposesParentRotationTest,
	"MotionWeaver.RefPose.Contract.SolverComposesParentRotation",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRefPoseSolverComposesParentRotationTest::RunTest(const FString& Parameters)
{
	// 参考旋转不是单位阵时，第二级开始的局部旋转必须先扣掉父级全局旋转。
	// 旧实现直接把世界方向当局部方向，会在第三根骨开始整体歪掉。
	const TArray<int32> Parents = MakeChainParents();
	TArray<FTransform> Reference;
	Reference.Add(FTransform(FQuat::Identity, FVector(0.0, 0.0, 0.0)));
	Reference.Add(FTransform(
		FQuat(FVector(0.0, 0.0, 1.0), FMath::DegreesToRadians(30.0)),
		FVector(10.0, 0.0, 0.0)));
	Reference.Add(FTransform(
		FQuat(FVector(1.0, 0.0, 0.0), FMath::DegreesToRadians(40.0)),
		FVector(0.0, 20.0, 0.0)));
	Reference.Add(FTransform(
		FQuat(FVector(0.0, 1.0, 0.0), FMath::DegreesToRadians(20.0)),
		FVector(0.0, 0.0, 30.0)));

	FRefPosePoseContract::FDecodedFrame Frame;
	Frame.PositionsMeters = {
		FVector3d(0.0, 0.0, 0.0),
		FVector3d(0.10, 0.0, 0.0),
		FVector3d(0.10, 0.20, 0.0),
		FVector3d(0.10, 0.20, 0.30),
	};
	Frame.Rotations.Init(FQuat4d::Identity, TestBoneCount);
	TArray<FRefPosePoseContract::FDecodedFrame> Frames;
	Frames.Add(Frame);

	TArray<TArray<FTransform>> Local;
	FRefPoseSkeletonSolver::FMetrics Metrics;
	FString Error;
	if (!TestTrue(TEXT("解算成功"), FRefPoseSkeletonSolver::Solve(
		Parents, Reference, Frames, Local, Metrics, Error)))
	{
		AddError(Error);
		return true;
	}
	TestEqual(TEXT("退化骨数"), Metrics.DegenerateBones, 0);
	TestEqual(TEXT("骨长偏差"), Metrics.MaxBoneLengthDeviationCm, 0.0);

	TArray<TArray<FVector>> Rebuilt;
	if (!TestTrue(TEXT("复算成功"), FRefPoseSkeletonSolver::RebuildSkeletonPositions(
		Parents, Local, Rebuilt, Error)))
	{
		AddError(Error);
		return true;
	}

	for (int32 Bone = 1; Bone < TestBoneCount; ++Bone)
	{
		const FVector3d Expected = Frame.PositionsMeters[Bone]
			- Frame.PositionsMeters[Parents[Bone]];
		const FVector3d ExpectedUe(Expected.Z, -Expected.X, Expected.Y);
		const FVector Actual = Rebuilt[0][Bone] - Rebuilt[0][Parents[Bone]];
		TestTrue(
			FString::Printf(TEXT("骨 %d 复算方向"), Bone),
			Actual.GetSafeNormal().Equals(FVector(ExpectedUe.GetSafeNormal()), 1e-6));
		TestTrue(
			FString::Printf(TEXT("骨 %d 复算骨长"), Bone),
			FMath::IsNearlyEqual(
				Actual.Size(),
				Reference[Bone].GetTranslation().Size(),
				1e-6));
	}

	// +X 应指向主子骨：骨 1 的子骨是骨 2，骨 2 的子骨是骨 3。
	TArray<FQuat> GlobalRotations;
	GlobalRotations.SetNum(TestBoneCount);
	for (int32 Bone = 0; Bone < TestBoneCount; ++Bone)
	{
		GlobalRotations[Bone] = Bone == 0
			? Local[0][Bone].GetRotation()
			: GlobalRotations[Parents[Bone]] * Local[0][Bone].GetRotation();
	}
	for (int32 Bone = 1; Bone < TestBoneCount - 1; ++Bone)
	{
		const FVector Axis =
			GlobalRotations[Bone].RotateVector(FVector::ForwardVector);
		const FVector3d ChildDirection =
			Frame.PositionsMeters[Bone + 1] - Frame.PositionsMeters[Bone];
		const FVector3d ChildDirectionUe(
			ChildDirection.Z, -ChildDirection.X, ChildDirection.Y);
		TestTrue(
			FString::Printf(TEXT("骨 %d 全局 +X 指向子骨"), Bone),
			Axis.Equals(FVector(ChildDirectionUe.GetSafeNormal()), 1e-6));
	}
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FRefPoseSolverRejectsBadHierarchyTest,
	"MotionWeaver.RefPose.Contract.SolverRejectsBadHierarchy",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRefPoseSolverRejectsBadHierarchyTest::RunTest(const FString& Parameters)
{
	// 父索引晚于自身：必须拒绝，否则后续循环会读到未初始化的父级。
	const TArray<int32> Parents = { -1, 2, 1, 0 };
	const TArray<FTransform> Reference = MakeChainReference();
	TArray<FRefPosePoseContract::FDecodedFrame> Frames;
	Frames.Add(MakeStraightFrame(1.0));

	TArray<TArray<FTransform>> Local;
	FRefPoseSkeletonSolver::FMetrics Metrics;
	FString Error;
	TestFalse(TEXT("非法层级被拒绝"), FRefPoseSkeletonSolver::Solve(
		Parents, Reference, Frames, Local, Metrics, Error));
	TestFalse(TEXT("有错误信息"), Error.IsEmpty());
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FRefPoseWindowBuilderTest,
	"MotionWeaver.RefPose.Window.Packing",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRefPoseWindowBuilderTest::RunTest(const FString& Parameters)
{
	using FRootSample = FRefPoseWindowBuilder::FRootSample;

	const int32 BoneCount = 2;
	const int32 Features = BoneCount * 9;

	FRefPoseWindowBuilder::FNormalization Norm;
	Norm.Mean.Init(0.0f, BoneCount * 3);
	Norm.Std.Init(1.0f, BoneCount * 3);

	auto MakeFrame = [BoneCount](double Offset)
	{
		FRefPoseWindowBuilder::FPoseFrame Pose;
		for (int32 Bone = 0; Bone < BoneCount; ++Bone)
		{
			Pose.PositionsMeters.Add(FVector3d(Offset + Bone, 0.0, 0.0));
			for (int32 Index = 0; Index < 6; ++Index)
			{
				Pose.Rotations6D.Add(Index % 2 == 0 ? 1.0f : 0.0f);
			}
		}
		return Pose;
	};

	TArray<FRefPoseWindowBuilder::FPoseFrame> History;
	TArray<FRefPoseWindowBuilder::FPoseFrame> Future;
	TArray<FRootSample> HistoryRoot;
	TArray<FRootSample> FutureRoot;
	for (int32 Frame = 0; Frame < FRefPoseWindowBuilder::HistoryFrames; ++Frame)
	{
		History.Add(MakeFrame(Frame * 0.01));
		FRootSample Sample;
		Sample.PositionMeters = FVector3d(Frame * 0.01, 0.0, 0.0);
		HistoryRoot.Add(Sample);
	}
	for (int32 Frame = 0; Frame < FRefPoseWindowBuilder::FutureFrames; ++Frame)
	{
		Future.Add(MakeFrame(0.24 + Frame * 0.01));
		FRootSample Sample;
		Sample.PositionMeters = FVector3d((24 + Frame) * 0.01, 0.0, 0.0);
		FutureRoot.Add(Sample);
	}

	const FRootSample Origin = HistoryRoot.Last();
	const TArray<int32> Offsets = { 12, 23 };

	FRefPoseWindowBuilder::FWindow Window;
	FString Error;
	if (!TestTrue(TEXT("装配成功"), FRefPoseWindowBuilder::Build(
		BoneCount, History, HistoryRoot, Future, Origin, FutureRoot,
		Offsets, Norm, Window, Error)))
	{
		AddError(Error);
		return true;
	}

	TestTrue(TEXT("输出尺寸"), Window.IsSized(BoneCount));

	// 训练侧 build_sample 的实际排布是「逐骨 9 维」：pos3 + rot6。
	// 这里直接按同一布局读回，抓「先全部位置、再全部旋转」这类块排布错误。
	// NNE 数值夹具是直接灌张量的，不会经过装配器，因此必须单独断言。
	for (int32 Bone = 0; Bone < BoneCount; ++Bone)
	{
		const float* HistoryBone = Window.History.GetData() + Bone * 9;
		TestEqual(
			FString::Printf(TEXT("history 骨 %d 位置 X"), Bone),
			HistoryBone[0], static_cast<float>(History[0].PositionsMeters[Bone].X));
		TestEqual(
			FString::Printf(TEXT("history 骨 %d 位置 Y"), Bone),
			HistoryBone[1], static_cast<float>(History[0].PositionsMeters[Bone].Y));
		TestEqual(
			FString::Printf(TEXT("history 骨 %d 位置 Z"), Bone),
			HistoryBone[2], static_cast<float>(History[0].PositionsMeters[Bone].Z));
		for (int32 Index = 0; Index < 6; ++Index)
		{
			TestEqual(
				FString::Printf(TEXT("history 骨 %d 旋转 %d"), Bone, Index),
				HistoryBone[3 + Index], History[0].Rotations6D[Bone * 6 + Index]);
		}

		const float* FutureBone = Window.Target.GetData() + Bone * 9;
		TestEqual(
			FString::Printf(TEXT("target 骨 %d 位置 X"), Bone),
			FutureBone[0], static_cast<float>(Future[0].PositionsMeters[Bone].X));
		TestEqual(
			FString::Printf(TEXT("target 骨 %d 位置 Y"), Bone),
			FutureBone[1], static_cast<float>(Future[0].PositionsMeters[Bone].Y));
		TestEqual(
			FString::Printf(TEXT("target 骨 %d 位置 Z"), Bone),
			FutureBone[2], static_cast<float>(Future[0].PositionsMeters[Bone].Z));
		for (int32 Index = 0; Index < 6; ++Index)
		{
			TestEqual(
				FString::Printf(TEXT("target 骨 %d 旋转 %d"), Bone, Index),
				FutureBone[3 + Index], Future[0].Rotations6D[Bone * 6 + Index]);
		}
	}

	// root_local：origin 是历史最后一帧（0.23），未来第一帧是 0.24，
	// 因此相对位移应为 +0.01 米，而不是 0。
	// 这条断言同时抓「origin 取错帧」这类差一帧错误。
	const float* FirstRoot = Window.RootPlan.GetData();
	TestEqual(TEXT("未来首帧相对位移"), FirstRoot[0], 0.01f);
	TestEqual(TEXT("未来首帧单位四元数 w"), FirstRoot[6], 1.0f);
	TestEqual(TEXT("未来末帧相对位移"),
		Window.RootPlan[(FRefPoseWindowBuilder::FutureFrames - 1) * 7], 0.24f);

	// mask 只有两帧为 1；其余 reference 整行为 0。
	int32 MaskedFrames = 0;
	for (int32 Frame = 0; Frame < FRefPoseWindowBuilder::FutureFrames; ++Frame)
	{
		if (Window.Mask[Frame] > 0.5f)
		{
			++MaskedFrames;
			// 选中帧必须与 target 逐元素相等（硬覆盖语义）。
			for (int32 Index = 0; Index < Features; ++Index)
			{
				TestEqual(
					FString::Printf(TEXT("参考帧 %d 特征 %d"), Frame, Index),
					Window.Reference[Frame * Features + Index],
					Window.Target[Frame * Features + Index]);
			}
		}
		else
		{
			for (int32 Index = 0; Index < Features; ++Index)
			{
				TestEqual(
					FString::Printf(TEXT("未选帧 %d 特征 %d 应为 0"), Frame, Index),
					Window.Reference[Frame * Features + Index],
					0.0f);
			}
		}
	}
	TestEqual(TEXT("参考帧数"), MaskedFrames, Offsets.Num());

	// 未覆盖档位必须被拒绝，不能静默当作已训练档位。
	FRefPoseWindowBuilder::FWindow Rejected;
	TestFalse(TEXT("未覆盖档位被拒绝"), FRefPoseWindowBuilder::Build(
		BoneCount, History, HistoryRoot, Future, Origin, FutureRoot,
		TArray<int32>{ 6, 18 }, Norm, Rejected, Error));
	return true;
}

#endif  // WITH_DEV_AUTOMATION_TESTS

namespace
{
	/** 构造一份最小映射用例：模型 3 骨对骨架 4 骨，其中一根无对应。 */
	void BuildMappingFixture(
		TArray<FName>& ModelNames,
		TArray<FName>& SkeletonNames,
		TMap<FName, FName>& Mapping)
	{
		ModelNames = { TEXT("Hips"), TEXT("Spine1"), TEXT("UnmappedBone") };
		SkeletonNames = { TEXT("root"), TEXT("pelvis"), TEXT("spine_01"), TEXT("spine_02") };
		Mapping.Add(TEXT("Hips"), TEXT("pelvis"));
		Mapping.Add(TEXT("Spine1"), TEXT("spine_01"));
	}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FRefPoseSkeletonMapBuildTest,
	"MotionWeaver.RefPose.Map.Build",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRefPoseSkeletonMapBuildTest::RunTest(const FString& Parameters)
{
	TArray<FName> ModelNames, SkeletonNames;
	TMap<FName, FName> Mapping;
	BuildMappingFixture(ModelNames, SkeletonNames, Mapping);

	FRefPoseSkeletonMap Map;
	TArray<FName> Unmapped;
	FString Error;
	if (!TestTrue(TEXT("映射建立成功"), FRefPoseSkeletonMap::Build(
		ModelNames, SkeletonNames, Mapping, Map, Unmapped, Error)))
	{
		AddError(Error);
		return true;
	}

	TestEqual(TEXT("映射骨数"), Map.MappedBoneCount, 2);
	TestEqual(TEXT("未映射骨数"), Unmapped.Num(), 1);
	TestEqual(TEXT("未映射骨名"), Unmapped[0], FName(TEXT("UnmappedBone")));
	TestEqual(TEXT("Hips -> pelvis"), Map.ModelToSkeleton[0], 1);
	TestEqual(TEXT("Spine1 -> spine_01"), Map.ModelToSkeleton[1], 2);
	TestEqual(TEXT("未映射槽位"), Map.ModelToSkeleton[2], INDEX_NONE);

	// 映射表写了目标但骨架里没有：必须落进未映射，不能静默丢骨。
	TMap<FName, FName> Broken = Mapping;
	Broken.Add(TEXT("UnmappedBone"), TEXT("does_not_exist"));
	FRefPoseSkeletonMap BrokenMap;
	TArray<FName> BrokenUnmapped;
	TestTrue(TEXT("残缺映射仍可建立"), FRefPoseSkeletonMap::Build(
		ModelNames, SkeletonNames, Broken, BrokenMap, BrokenUnmapped, Error));
	TestEqual(TEXT("不存在的目标骨被记为未映射"), BrokenUnmapped.Num(), 1);
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FRefPoseSkeletonMapRetargetTest,
	"MotionWeaver.RefPose.Map.RetargetPreservesSkeletonBoneLength",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRefPoseSkeletonMapRetargetTest::RunTest(const FString& Parameters)
{
	TArray<FName> ModelNames, SkeletonNames;
	TMap<FName, FName> Mapping;
	BuildMappingFixture(ModelNames, SkeletonNames, Mapping);

	FRefPoseSkeletonMap Map;
	TArray<FName> Unmapped;
	FString Error;
	if (!TestTrue(TEXT("映射建立成功"), FRefPoseSkeletonMap::Build(
		ModelNames, SkeletonNames, Mapping, Map, Unmapped, Error)))
	{
		AddError(Error);
		return true;
	}

	// UE 参考骨长：pelvis 与 spine_01 各 100 厘米；spine_02 未映射。
	TArray<FTransform> Reference;
	Reference.Add(FTransform(FQuat::Identity, FVector(0.0, 0.0, 0.0)));
	Reference.Add(FTransform(FQuat::Identity, FVector(100.0, 0.0, 0.0)));
	Reference.Add(FTransform(FQuat::Identity, FVector(0.0, 100.0, 0.0)));
	Reference.Add(FTransform(FQuat::Identity, FVector(0.0, 0.0, 100.0)));

	// 模型给的平移长度完全不同（1 与 3 厘米），必须被参考骨长替换。
	TArray<TArray<FTransform>> ModelLocal;
	TArray<FTransform> Frame;
	Frame.Add(FTransform(FQuat::Identity, FVector(1.0, 0.0, 0.0)));
	Frame.Add(FTransform(FQuat::Identity, FVector(0.0, 0.0, 3.0)));
	Frame.Add(FTransform(FQuat::Identity, FVector(9.0, 0.0, 0.0)));
	ModelLocal.Add(Frame);

	const TArray<int32> ModelParents = { -1, 0, 1 };
	const TArray<int32> SkeletonParents = { -1, 0, 1, 2 };
	TArray<TArray<FTransform>> Out;
	if (!TestTrue(TEXT("重定向成功"), Map.Retarget(
		ModelLocal, ModelParents, Reference, SkeletonParents, Out, Error)))
	{
		AddError(Error);
		return true;
	}

	TestEqual(TEXT("帧数"), Out.Num(), 1);
	// 方向来自模型、长度来自 UE 骨架。
	const FVector Pelvis = Out[0][1].GetTranslation();
	TestTrue(TEXT("pelvis 长度等于参考"),
		FMath::IsNearlyEqual(Pelvis.Size(), 100.0, 1e-6));
	TestTrue(TEXT("pelvis 方向为 +X"), Pelvis.GetSafeNormal().Equals(FVector(1.0, 0.0, 0.0), 1e-6));

	const FVector Spine = Out[0][2].GetTranslation();
	TestTrue(TEXT("spine_01 方向为 +Z"), Spine.GetSafeNormal().Equals(FVector(0.0, 0.0, 1.0), 1e-6));

	// 未映射的骨保持参考姿态，而不是被清成零。
	TestTrue(TEXT("未映射骨保持参考"),
		Out[0][3].GetTranslation().Equals(Reference[3].GetTranslation(), 1e-6));
	// root 也没有模型骨对应，保持参考。
	TestTrue(TEXT("root 保持参考"),
		Out[0][0].GetTranslation().Equals(Reference[0].GetTranslation(), 1e-6));
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FRefPoseSkeletonMapIntermediateChainTest,
	"MotionWeaver.RefPose.Map.RetargetThroughUnmappedIntermediate",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRefPoseSkeletonMapIntermediateChainTest::RunTest(const FString& Parameters)
{
	// UE 的 Chest(spine_03) 与 Neck1(neck_01) 之间还有 spine_04/spine_05。
	// 它们没有模型对应，必须保持参考旋转；映射骨的局部旋转要扣掉这段参考链，
	// 否则头颈会被这两根参考骨再转一次。
	const TArray<FName> ModelNames = {
		TEXT("Hips"), TEXT("Spine1"), TEXT("Chest"), TEXT("Neck1") };
	const TArray<FName> SkeletonNames = {
		TEXT("pelvis"), TEXT("spine_01"), TEXT("spine_02"), TEXT("spine_03"),
		TEXT("spine_04"), TEXT("spine_05"), TEXT("neck_01") };
	TMap<FName, FName> Mapping;
	Mapping.Add(TEXT("Hips"), TEXT("pelvis"));
	Mapping.Add(TEXT("Spine1"), TEXT("spine_01"));
	Mapping.Add(TEXT("Chest"), TEXT("spine_03"));
	Mapping.Add(TEXT("Neck1"), TEXT("neck_01"));

	FRefPoseSkeletonMap Map;
	TArray<FName> Unmapped;
	FString Error;
	if (!TestTrue(TEXT("映射建立成功"), FRefPoseSkeletonMap::Build(
		ModelNames, SkeletonNames, Mapping, Map, Unmapped, Error)))
	{
		AddError(Error);
		return true;
	}

	const TArray<int32> ModelParents = { -1, 0, 1, 2 };
	const TArray<int32> SkeletonParents = { -1, 0, 1, 2, 3, 4, 5 };

	const FQuat Spine2Rotation(FVector(0.0, 0.0, 1.0), FMath::DegreesToRadians(40.0));
	const FQuat Spine4Rotation(FVector(1.0, 0.0, 0.0), FMath::DegreesToRadians(30.0));
	const FQuat Spine5Rotation(FVector(0.0, 1.0, 0.0), FMath::DegreesToRadians(20.0));
	const FQuat Neck1Rotation(FVector(0.0, 0.0, 1.0), FMath::DegreesToRadians(10.0));

	TArray<FTransform> Reference;
	Reference.Add(FTransform(FQuat::Identity, FVector(0.0, 0.0, 0.0)));
	Reference.Add(FTransform(FQuat::Identity, FVector(100.0, 0.0, 0.0)));
	Reference.Add(FTransform(Spine2Rotation, FVector(100.0, 0.0, 0.0)));
	Reference.Add(FTransform(FQuat::Identity, FVector(100.0, 0.0, 0.0)));
	Reference.Add(FTransform(Spine4Rotation, FVector(100.0, 0.0, 0.0)));
	Reference.Add(FTransform(Spine5Rotation, FVector(100.0, 0.0, 0.0)));
	Reference.Add(FTransform(FQuat::Identity, FVector(100.0, 0.0, 0.0)));

	TArray<FTransform> ModelFrame;
	ModelFrame.Add(FTransform(FQuat::Identity, FVector(0.0, 0.0, 0.0)));
	ModelFrame.Add(FTransform(FQuat::Identity, FVector(100.0, 0.0, 0.0)));
	ModelFrame.Add(FTransform(FQuat::Identity, FVector(100.0, 0.0, 0.0)));
	ModelFrame.Add(FTransform(Neck1Rotation, FVector(5.0, 0.0, 0.0)));
	TArray<TArray<FTransform>> ModelLocal;
	ModelLocal.Add(ModelFrame);

	TArray<TArray<FTransform>> Out;
	if (!TestTrue(TEXT("重定向成功"), Map.Retarget(
		ModelLocal, ModelParents, Reference, SkeletonParents, Out, Error)))
	{
		AddError(Error);
		return true;
	}

	// 复算 UE 全局旋转/位置，验证 neck_01 的全局朝向和方向都回到了模型语义。
	TArray<FQuat> GlobalRotations;
	TArray<FVector> GlobalPositions;
	GlobalRotations.SetNum(SkeletonNames.Num());
	GlobalPositions.SetNum(SkeletonNames.Num());
	for (int32 Index = 0; Index < SkeletonNames.Num(); ++Index)
	{
		const int32 Parent = SkeletonParents[Index];
		if (Parent < 0)
		{
			GlobalRotations[Index] = Out[0][Index].GetRotation();
			GlobalPositions[Index] = Out[0][Index].GetTranslation();
		}
		else
		{
			GlobalRotations[Index] =
				GlobalRotations[Parent] * Out[0][Index].GetRotation();
			GlobalPositions[Index] = GlobalPositions[Parent]
				+ GlobalRotations[Parent].RotateVector(Out[0][Index].GetTranslation());
		}
	}

	const int32 NeckIndex = 6;
	const int32 ChestIndex = 3;
	const int32 NeckModelIndex = 3;
	const FQuat ExpectedGlobal =
		GlobalRotations[ChestIndex] * ModelFrame[NeckModelIndex].GetRotation();
	const FVector ExpectedAxis =
		ExpectedGlobal.RotateVector(FVector::ForwardVector);
	const FVector ActualAxis =
		GlobalRotations[NeckIndex].RotateVector(FVector::ForwardVector);
	TestTrue(TEXT("neck_01 全局朝向穿过中间参考骨"),
		ActualAxis.Equals(ExpectedAxis, 1e-6));

	const FVector ExpectedDirection =
		GlobalRotations[ChestIndex].RotateVector(ModelFrame[NeckModelIndex].GetTranslation());
	const FVector ActualDirection =
		GlobalPositions[NeckIndex] - GlobalPositions[SkeletonParents[NeckIndex]];
	TestTrue(TEXT("neck_01 全局方向穿过中间参考骨"),
		ActualDirection.GetSafeNormal().Equals(
			ExpectedDirection.GetSafeNormal(), 1e-6));
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FRefPoseSkeletonMapDefaultCoverageTest,
	"MotionWeaver.RefPose.Map.DefaultCoverageOnUe5Mannequin",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRefPoseSkeletonMapDefaultCoverageTest::RunTest(const FString& Parameters)
{
	// 真实模型包 + UE5 Mannequin 的覆盖面门禁：数量、缺口集合、父级一致性。
	// 缺数据时明确跳过，而不是把空骨架当成通过。
	const FString PackageDir =
		FPlatformMisc::GetEnvironmentVariable(TEXT("MW_REFPOSE_UE_PACKAGE"));
	if (PackageDir.IsEmpty())
	{
		AddInfo(TEXT("未设置 MW_REFPOSE_UE_PACKAGE，跳过真实骨架覆盖检查。"));
		return true;
	}

	TStrongObjectPtr<URefPoseModelAsset> Model(NewObject<URefPoseModelAsset>());
	FString Error;
	if (!TestTrue(TEXT("读取模型骨架契约"),
		Model->LoadContractFromPackage(PackageDir, Error)))
	{
		AddError(Error);
		return true;
	}

	USkeletalMesh* Mesh = Cast<USkeletalMesh>(FSoftObjectPath(
		TEXT("/Game/Characters/UE5_Mannequins/Meshes/SKM_Manny")).TryLoad());
	if (Mesh == nullptr || Mesh->GetSkeleton() == nullptr)
	{
		AddInfo(TEXT("SKM_Manny 不可用，跳过真实骨架覆盖检查。"));
		return true;
	}

	const FReferenceSkeleton& Reference = Mesh->GetSkeleton()->GetReferenceSkeleton();
	TArray<FName> ModelNames;
	TArray<int32> ModelParents;
	ModelNames.Reserve(Model->Bones.Num());
	ModelParents.Reserve(Model->Bones.Num());
	for (const FRefPoseBoneContract& Bone : Model->Bones)
	{
		ModelNames.Add(Bone.Name);
		ModelParents.Add(Bone.Parent);
	}
	TArray<FName> SkeletonNames;
	TArray<int32> SkeletonParents;
	SkeletonNames.Reserve(Reference.GetNum());
	SkeletonParents.Reserve(Reference.GetNum());
	for (int32 Index = 0; Index < Reference.GetNum(); ++Index)
	{
		SkeletonNames.Add(Reference.GetBoneName(Index));
		SkeletonParents.Add(Reference.GetParentIndex(Index));
	}

	FRefPoseSkeletonMap Map;
	TArray<FName> Unmapped;
	if (!TestTrue(TEXT("默认映射建立成功"), FRefPoseSkeletonMap::Build(
		ModelNames, SkeletonNames, FRefPoseSkeletonMap::GetDefaultUe5Mapping(),
		Map, Unmapped, Error)))
	{
		AddError(Error);
		return true;
	}

	TestEqual(TEXT("默认映射覆盖骨数"), Map.MappedBoneCount, 62);
	TestEqual(TEXT("未映射骨数"), Unmapped.Num(), 16);

	TSet<FName> ExpectedUnmapped = {
		TEXT("HeadEnd"), TEXT("Jaw"), TEXT("LeftEye"), TEXT("RightEye"),
		TEXT("LeftHandThumbEnd"), TEXT("LeftHandIndexEnd"),
		TEXT("LeftHandMiddleEnd"), TEXT("LeftHandRingEnd"),
		TEXT("LeftHandPinkyEnd"),
		TEXT("RightHandThumbEnd"), TEXT("RightHandIndexEnd"),
		TEXT("RightHandMiddleEnd"), TEXT("RightHandRingEnd"),
		TEXT("RightHandPinkyEnd"),
		TEXT("LeftToeEnd"), TEXT("RightToeEnd"),
	};
	for (const FName& Name : Unmapped)
	{
		TestTrue(
			FString::Printf(TEXT("未映射骨 %s 属于已知缺口"), *Name.ToString()),
			ExpectedUnmapped.Contains(Name));
		ExpectedUnmapped.Remove(Name);
	}
	TestEqual(TEXT("已知缺口全部出现"), ExpectedUnmapped.Num(), 0);

	// UE 里可能有未映射中间骨；映射骨的最近映射祖先必须等于模型父骨。
	auto NearestMappedAncestor = [&Map, &SkeletonParents](int32 Index) -> int32
	{
		while (Index >= 0 && Map.SkeletonToModel[Index] == INDEX_NONE)
		{
			Index = SkeletonParents[Index];
		}
		return Index;
	};
	for (int32 ModelIndex = 0; ModelIndex < ModelNames.Num(); ++ModelIndex)
	{
		const int32 SkeletonIndex = Map.ModelToSkeleton[ModelIndex];
		const int32 ModelParent = ModelParents[ModelIndex];
		if (SkeletonIndex == INDEX_NONE || ModelParent < 0)
		{
			continue;
		}
		const int32 Expected = Map.ModelToSkeleton[ModelParent];
		const int32 SkeletonParent = SkeletonParents[SkeletonIndex];
		const int32 Actual = SkeletonParent >= 0
			? NearestMappedAncestor(SkeletonParent)
			: INDEX_NONE;
		TestEqual(
			FString::Printf(TEXT("%s 的父子映射"), *ModelNames[ModelIndex].ToString()),
			Actual, Expected);
	}
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FRefPoseRealSkeletonConversionTest,
	"MotionWeaver.RefPose.Input.RealSkeletonConversion",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FRefPoseRealSkeletonConversionTest::RunTest(const FString& Parameters)
{
	// 用真实模型契约 + SKM_Manny 跑一遍 161 → 78 的转换。
	// 合成用例覆盖公式；这一条覆盖实际层级、骨名与未映射末端骨。
	const FString PackageDir =
		FPlatformMisc::GetEnvironmentVariable(TEXT("MW_REFPOSE_UE_PACKAGE"));
	if (PackageDir.IsEmpty())
	{
		AddInfo(TEXT("未设置 MW_REFPOSE_UE_PACKAGE，跳过真实骨架转换检查。"));
		return true;
	}

	TStrongObjectPtr<URefPoseModelAsset> Model(NewObject<URefPoseModelAsset>());
	FString Error;
	if (!TestTrue(TEXT("读取模型骨架契约"),
		Model->LoadContractFromPackage(PackageDir, Error)))
	{
		AddError(Error);
		return true;
	}

	USkeletalMesh* Mesh = Cast<USkeletalMesh>(FSoftObjectPath(
		TEXT("/Game/Characters/UE5_Mannequins/Meshes/SKM_Manny")).TryLoad());
	if (Mesh == nullptr || Mesh->GetSkeleton() == nullptr)
	{
		AddInfo(TEXT("SKM_Manny 不可用，跳过真实骨架转换检查。"));
		return true;
	}
	const FReferenceSkeleton& Reference = Mesh->GetSkeleton()->GetReferenceSkeleton();
	const TArray<FTransform>& ReferenceLocal = Reference.GetRefBonePose();

	TArray<FName> ModelNames;
	for (const FRefPoseBoneContract& Bone : Model->Bones)
	{
		ModelNames.Add(Bone.Name);
	}
	TArray<FName> SkeletonNames;
	TArray<int32> SkeletonParents;
	for (int32 Index = 0; Index < Reference.GetNum(); ++Index)
	{
		SkeletonNames.Add(Reference.GetBoneName(Index));
		SkeletonParents.Add(Reference.GetParentIndex(Index));
	}

	FRefPoseSkeletonMap Map;
	TArray<FName> Unmapped;
	if (!TestTrue(TEXT("默认映射建立成功"), FRefPoseSkeletonMap::Build(
		ModelNames, SkeletonNames, FRefPoseSkeletonMap::GetDefaultUe5Mapping(),
		Map, Unmapped, Error)))
	{
		AddError(Error);
		return true;
	}

	// 1) 求解器参考：长度取模型、朝向取 UE 相对参考。
	TArray<FTransform> SolverReference;
	if (!TestTrue(TEXT("真实骨架求解器参考构造成功"),
		FRefPoseGenerator::BuildSolverReference(
			*Model, Map, ReferenceLocal, SkeletonParents, SolverReference, Error)))
	{
		AddError(Error);
		return true;
	}
	TestEqual(TEXT("求解器参考骨数"), SolverReference.Num(), Model->BoneCount);
	TestRotationEquals(
		*this, TEXT("Root 参考旋转为单位"),
		FQuat4d(SolverReference[0].GetRotation()), FQuat4d::Identity);

	TArray<FTransform> SkeletonGlobal;
	SkeletonGlobal.SetNum(SkeletonParents.Num());
	for (int32 Index = 0; Index < SkeletonParents.Num(); ++Index)
	{
		const int32 Parent = SkeletonParents[Index];
		SkeletonGlobal[Index] = Parent < 0
			? ReferenceLocal[Index]
			: SkeletonGlobal[Parent] * ReferenceLocal[Index];
	}
	const FQuat4d SkeletonRootInv =
		FQuat4d(SkeletonGlobal[0].GetRotation()).GetNormalized().Inverse();

	TArray<FQuat4d> SolverGlobal;
	SolverGlobal.SetNum(Model->BoneCount);
	for (int32 ModelIndex = 0; ModelIndex < Model->BoneCount; ++ModelIndex)
	{
		const int32 Parent = Model->Bones[ModelIndex].Parent;
		const FQuat4d Local(SolverReference[ModelIndex].GetRotation());
		SolverGlobal[ModelIndex] = Parent < 0
			? Local.GetNormalized()
			: (SolverGlobal[Parent] * Local).GetNormalized();

		const int32 SkeletonIndex = Map.ModelToSkeleton[ModelIndex];
		if (SkeletonIndex != INDEX_NONE && Parent >= 0)
		{
			const FQuat4d Expected = (
				SkeletonRootInv
				* FQuat4d(SkeletonGlobal[SkeletonIndex].GetRotation()).GetNormalized()
			).GetNormalized();
			TestRotationEquals(
				*this,
				*FString::Printf(TEXT("%s 的静止朝向与 UE 参考一致"),
					*Model->Bones[ModelIndex].Name.ToString()),
				SolverGlobal[ModelIndex], Expected);
		}

		// 长度必须来自模型契约，不能悄悄变成 UE 骨长。
		TestTrue(
			FString::Printf(TEXT("%s 的参考长度来自模型"),
				*Model->Bones[ModelIndex].Name.ToString()),
			FMath::IsNearlyEqual(
				SolverReference[ModelIndex].GetTranslation().Size(),
				Model->Bones[ModelIndex].LocalPositionMeters.Size() * 100.0,
				1e-7));
	}

	// 2) UE 参考姿态 → 模型输入：Root 在原点，已映射骨与组件空间一致，
	//    未映射末端骨按模型参考偏移挂在父骨上。
	FRefPoseGenerator::FPoseInput Pose;
	const FRefPoseWindowBuilder::FRootSample Track =
		MakeTrackSample(FVector3d::ZeroVector, 0.0);
	if (!TestTrue(TEXT("真实参考姿态转模型输入成功"),
		FRefPoseGenerator::SkeletonPoseToPoseInput(
			*Model, Map, ReferenceLocal, SkeletonParents, Track, Pose, Error)))
	{
		AddError(Error);
		return true;
	}
	TestEqual(TEXT("模型输入骨数"), Pose.PositionsMeters.Num(), Model->BoneCount);
	TestEqual(TEXT("模型输入旋转长度"), Pose.Rotations6D.Num(), Model->BoneCount * 6);
	TestTrue(TEXT("Root 在原点"),
		Pose.PositionsMeters[0].Equals(FVector3d::ZeroVector, 1e-9));

	for (int32 ModelIndex = 1; ModelIndex < Model->BoneCount; ++ModelIndex)
	{
		const int32 SkeletonIndex = Map.ModelToSkeleton[ModelIndex];
		if (SkeletonIndex != INDEX_NONE)
		{
			const FVector Expected = FRefPosePoseContract::ToMotionPosition(
				SkeletonRootInv.RotateVector(
					SkeletonGlobal[SkeletonIndex].GetTranslation()
					- SkeletonGlobal[0].GetTranslation()));
			TestTrue(
				FString::Printf(TEXT("%s 的静止位置与 UE 组件空间一致"),
					*Model->Bones[ModelIndex].Name.ToString()),
				Pose.PositionsMeters[ModelIndex].Equals(
					FVector3d(Expected), 1e-9));
		}
		else
		{
			const int32 Parent = Model->Bones[ModelIndex].Parent;
			TestTrue(
				FString::Printf(TEXT("%s 的父骨已映射"),
					*Model->Bones[ModelIndex].Name.ToString()),
				Parent >= 0 && Map.ModelToSkeleton[Parent] != INDEX_NONE);
			if (Parent >= 0)
			{
				const double Distance =
					(Pose.PositionsMeters[ModelIndex] - Pose.PositionsMeters[Parent]).Size();
				TestTrue(
					FString::Printf(TEXT("%s 按模型参考偏移挂到父骨"),
						*Model->Bones[ModelIndex].Name.ToString()),
					FMath::IsNearlyEqual(
						Distance,
						Model->Bones[ModelIndex].LocalPositionMeters.Size(),
						1e-9));
			}
		}
	}
	return true;
}
