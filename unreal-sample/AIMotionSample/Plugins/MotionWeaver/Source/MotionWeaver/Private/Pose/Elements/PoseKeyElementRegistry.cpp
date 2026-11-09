// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Elements/PoseKeyElementRegistry.h"

#include "Pose/Elements/PoseKeyElementData.h"
#include "Pose/PoseGroupProxyAsset.h"
#include "Core/MotionWeaverLog.h"

#include "Elements/Framework/TypedElementRegistry.h"
#include "Elements/Interfaces/TypedElementWorldInterface.h"

// RTTI 的定义必须放在 cpp 里（TypedElementData.h:25 的注释要求），
// 头文件里只放声明。
UE_DEFINE_TYPED_ELEMENT_DATA_RTTI(FPoseKeyElementData)

const FName FPoseKeyElementRegistry::ElementTypeName(TEXT("PoseKey"));

namespace
{
	/** 已注册过就不再注册：重复注册会被引擎断言拦下。 */
	bool bRegistered = false;

	/**
	 * 创建出来的元素用的是带类型的 owner，析构前必须 Destroy。
	 * 这里只保存 owner（它自己会维护 id），供销毁时使用。
	 */
	struct FHandleRecord
	{
		TTypedElementOwner<FPoseKeyElementData> Owner;
	};

	/**
	 * 元素序号 -> owner。销毁时按序号找回来。
	 * 用数值而不是 FTypedElementId 作键：后者的拷贝构造被删除，
	 * 放不进 TMap；元素序号在一次注册周期内唯一。
	 */
	TMap<FTypedHandleElementId, TSharedPtr<FHandleRecord>> LiveHandles;
}

void FPoseKeyElementRegistry::Register()
{
	if (bRegistered)
	{
		return;
	}

	UTypedElementRegistry* Registry = UTypedElementRegistry::GetInstance();
	if (Registry == nullptr)
	{
		// Registry 由 TypedElementFramework 模块提供，正常情况下在引擎启动期就有。
		// 拿不到说明模块没加载或调用时机过早，直接放弃注册而不是崩掉。
		UE_LOG(LogMotionWeaver, Error,
			TEXT("[PoseKeyElement] Registry 不可用，元素类型注册跳过"));
		return;
	}

	// bSupportScriptHandles = true：允许从蓝图/脚本侧持有句柄。
	// 与引擎自身注册 Actor / Component 元素时一致（EngineElements.cpp:53/66）。
	Registry->RegisterElementType<FPoseKeyElementData, true>(ElementTypeName);

	// 世界变换接口在 Editor 模块里注册（它需要 GEditor，属于编辑器侧）。
	// 那个模块启动时自己调用 FPoseKeyElementRegistry::RegisterWorldInterface()。

	bRegistered = true;

	const FTypedHandleTypeId TypeId = Registry->GetRegisteredElementTypeId(ElementTypeName);
	UE_LOG(LogMotionWeaver, Log,
		TEXT("[PoseKeyElement] 注册完成 typeId=%d name=%s"),
		static_cast<int32>(TypeId), *ElementTypeName.ToString());
}

void FPoseKeyElementRegistry::Unregister()
{
	// owner 的析构里有 checkf(!IsSet())（TypedElementHandle.h:437）：
	// 只要还有一条记录带着未清空的 owner 被析构，就会断言。
	// 所以必须在记录还活着的时候把每个 owner 都清干净。
	for (auto& Pair : LiveHandles)
	{
		if (!Pair.Value.IsValid() || !Pair.Value->Owner.IsSet())
		{
			continue;
		}

		// 模块关闭期只做一件事：让 owner 释放引用并把自己置空。
		//
		// 不在这里调 Registry->DestroyElement：
		// 它内部第一步是 GetRegisteredElementTypeFromId，拿不到类型就 checkf 崩掉；
		// 而模块关闭时引擎很可能已经先注销了元素类型。
		// 一旦它在半路崩掉，owner 也没被清空，紧接着记录析构又会撞
		// checkf(!IsSet())（TypedElementHandle.h:437）——正是关闭编辑器时的崩溃。
		//
		// Private_DestroyReleaseRef 是公开接口（TypedElementHandle.h:573），
		// 它做 UnregisterRef + Private_DestroyNoRef，正好是这里需要的：
		// 引用计数正常归还，owner 自己变成未设置状态，析构就不会断言。
		// 元素数据本身随 Registry 一起随模块销毁，不跨模块泄漏。
		Pair.Value->Owner.Private_DestroyReleaseRef();
	}
	LiveHandles.Empty();

	bRegistered = false;
}

bool FPoseKeyElementRegistry::IsRegistered()
{
	return bRegistered;
}

void FPoseKeyElementRegistry::RegisterWorldInterface(UObject* WorldInterfaceInstance)
{
	if (!bRegistered || WorldInterfaceInstance == nullptr)
	{
		UE_LOG(LogMotionWeaver, Error,
			TEXT("[PoseKeyElement] 注册世界接口失败：类型未注册或实例为空"));
		return;
	}

	UTypedElementRegistry* Registry = UTypedElementRegistry::GetInstance();
	if (Registry == nullptr)
	{
		return;
	}

	// 编辑器变换手柄只驱动实现了 ITypedElementWorldInterface 的元素，
	// 不挂这个接口，元素能创建但永远进不了 gizmo。
	Registry->RegisterElementInterface<ITypedElementWorldInterface>(
		ElementTypeName, WorldInterfaceInstance);

	UE_LOG(LogMotionWeaver, Log, TEXT("[PoseKeyElement] 世界变换接口已挂载"));
}

FTypedElementHandle FPoseKeyElementRegistry::CreateHandleForKey(
	UPoseGroupProxyAsset* Asset, int32 KeyIndex)
{
	if (!bRegistered || Asset == nullptr || KeyIndex == INDEX_NONE)
	{
		return FTypedElementHandle();
	}

	UTypedElementRegistry* Registry = UTypedElementRegistry::GetInstance();
	if (Registry == nullptr)
	{
		return FTypedElementHandle();
	}

	TSharedPtr<FHandleRecord> Record = MakeShared<FHandleRecord>();
	Record->Owner = Registry->CreateElement<FPoseKeyElementData>(ElementTypeName);

	if (!Record->Owner.IsSet())
	{
		return FTypedElementHandle();
	}

	// 把「指向哪个资产的哪个 Key」写进元素数据。
	// 这是唯一的联系，元素本身不复制任何姿态数据。
	if (FPoseKeyElementData* Data = Record->Owner.GetData())
	{
		Data->Asset = Asset;
		Data->KeyIndex = KeyIndex;
	}

	// FTypedElementId 的拷贝构造被删除，不能持有它的副本；
	// 先取数值存进表，再用 owner 自己的 AcquireHandle 拿 handle。
	const FTypedHandleElementId ElementIdValue = Record->Owner.GetId().GetElementId();
	LiveHandles.Add(ElementIdValue, Record);

	UE_LOG(LogMotionWeaver, Log,
		TEXT("[PoseKeyElement] 创建 handle 成功 asset=%s key=%d"),
		*Asset->GetName(), KeyIndex);

	// AcquireHandle 会加引用，使用方必须配对 Release。
	return Record->Owner.AcquireHandle();
}

void FPoseKeyElementRegistry::DestroyHandle(FTypedElementHandle& Handle)
{
	if (!Handle.IsSet())
	{
		return;
	}

	// 关键顺序：先把调用方句柄持有的引用计数释放掉，再去销毁元素本体。
	//
	// 句柄析构时会 ReleaseRef，那个操作要访问元素内部数据；
	// 如果先 DestroyElement 再让句柄析构，就是在访问已经销毁的数据——
	// 表现为 EXCEPTION_ACCESS_VIOLATION（读 0x0000000400000084 这类野地址）。
	const FTypedHandleElementId ElementIdValue = Handle.GetId().GetElementId();
	Handle = FTypedElementHandle();

	UTypedElementRegistry* Registry = UTypedElementRegistry::GetInstance();
	if (Registry == nullptr)
	{
		return;
	}

	TSharedPtr<FHandleRecord> Record;
	if (!LiveHandles.RemoveAndCopyValue(ElementIdValue, Record) || !Record.IsValid())
	{
		return;
	}

	if (Record->Owner.IsSet())
	{
		// owner 析构里有 checkf(!IsSet())，必须先销毁再让它析构。
		// 销毁接口在 Registry 上，owner 自己没有。
		Registry->DestroyElement(Record->Owner);
	}

	UE_LOG(LogMotionWeaver, Log, TEXT("[PoseKeyElement] 销毁 handle 成功"));
}
