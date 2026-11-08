// Copyright ZhaoZining. All Rights Reserved.

#include "Pose/Extraction/PoseToolbar.h"

#include "Pose/PoseProxyAsset.h"
#include "Pose/Extraction/PoseExtractor.h"

#include "ContentBrowserModule.h"
#include "Editor.h"
#include "Framework/MultiBox/MultiBoxBuilder.h"
#include "Framework/Notifications/NotificationManager.h"
#include "IAnimationEditor.h"
#include "IAnimationEditorModule.h"
#include "IContentBrowserSingleton.h"
#include "IPersonaToolkit.h"
#include "Modules/ModuleManager.h"
#include "Subsystems/AssetEditorSubsystem.h"
#include "Styling/AppStyle.h"
#include "Widgets/Notifications/SNotificationList.h"

#define LOCTEXT_NAMESPACE "PoseToolbar"

IAnimationEditorModule::FAnimationEditorToolbarExtender FPoseToolbar::RegisteredExtender;

namespace
{
	/** 抽帧目标目录；后续可以改成可配置项。 */
	const TCHAR* PoseOutputPath = TEXT("/Game/Poses");

	/** 从编辑器实例取 Persona 工具包；取不到返回 nullptr。 */
	IPersonaToolkit* ResolveToolkit(TWeakPtr<IAnimationEditor> WeakAnimationEditor)
	{
		const TSharedPtr<IAnimationEditor> AnimationEditor = WeakAnimationEditor.Pin();
		if (!AnimationEditor.IsValid())
		{
			return nullptr;
		}

		return &AnimationEditor->GetPersonaToolkit().Get();
	}

	/** 打开资产编辑器；资产已被 GC 时静默忽略。 */
	void OpenPoseAsset(TWeakObjectPtr<UPoseProxyAsset> WeakAsset)
	{
		UPoseProxyAsset* Asset = WeakAsset.Get();
		if (Asset == nullptr)
		{
			return;
		}

		if (UAssetEditorSubsystem* AssetEditorSubsystem =
				GEditor != nullptr ? GEditor->GetEditorSubsystem<UAssetEditorSubsystem>() : nullptr)
		{
			AssetEditorSubsystem->OpenEditorForAsset(Asset);
		}
	}

	/** 在内容浏览器里定位并选中资产。 */
	void RevealPoseAsset(TWeakObjectPtr<UPoseProxyAsset> WeakAsset)
	{
		UPoseProxyAsset* Asset = WeakAsset.Get();
		if (Asset == nullptr)
		{
			return;
		}

		TArray<UObject*> AssetsToSync;
		AssetsToSync.Add(Asset);

		FContentBrowserModule& ContentBrowserModule =
			FModuleManager::LoadModuleChecked<FContentBrowserModule>("ContentBrowser");
		ContentBrowserModule.Get().SyncBrowserToAssets(AssetsToSync);
	}

	/**
	 * 通知与它自己的关闭按钮之间的桥。
	 *
	 * 关闭按钮的回调必须在 AddNotification 之前就绑好，但那时还没有 Item；
	 * 用一个共享槽位先占好位置，AddNotification 返回后再把 Item 填进去。
	 */
	struct FNotificationCloseHandle
	{
		TWeakPtr<SNotificationItem> Item;

		void Dismiss() const
		{
			if (const TSharedPtr<SNotificationItem> Pinned = Item.Pin())
			{
				Pinned->Fadeout();
			}
		}
	};

	/**
	 * 统一的用户反馈出口。
	 *
	 * 成功时给「打开」「在内容浏览器中显示」「关闭」三个按钮，
	 * 失败时只给「关闭」——引擎的通知默认只靠超时消失，长通知不手动关掉很碍事。
	 */
	void NotifyExtraction(const FText& Message, bool bSuccess, TWeakObjectPtr<UPoseProxyAsset> NewAsset = nullptr)
	{
		FNotificationInfo Info(Message);
		Info.bUseSuccessFailIcons = true;

		const SNotificationItem::ECompletionState VisibleState =
			bSuccess ? SNotificationItem::CS_Success : SNotificationItem::CS_Fail;

		Info.ExpireDuration = bSuccess ? 20.0f : 10.0f;

		if (bSuccess && NewAsset.IsValid())
		{
			Info.ButtonDetails.Add(FNotificationButtonInfo(
				LOCTEXT("OpenPoseAssetButton", "打开"),
				LOCTEXT("OpenPoseAssetButtonTip", "在姿势代理编辑器里打开这个资产。"),
				FSimpleDelegate::CreateStatic(&OpenPoseAsset, NewAsset),
				VisibleState));

			Info.ButtonDetails.Add(FNotificationButtonInfo(
				LOCTEXT("RevealPoseAssetButton", "在内容浏览器中显示"),
				LOCTEXT("RevealPoseAssetButtonTip", "定位并选中这个资产。"),
				FSimpleDelegate::CreateStatic(&RevealPoseAsset, NewAsset),
				VisibleState));
		}

		// 关闭按钮的回调在本条通知自身的生命周期内有效，用共享句柄接住 Item。
		const TSharedRef<FNotificationCloseHandle> CloseHandle = MakeShared<FNotificationCloseHandle>();

		Info.ButtonDetails.Add(FNotificationButtonInfo(
			LOCTEXT("DismissNotificationButton", "关闭"),
			LOCTEXT("DismissNotificationButtonTip", "关闭这条通知。"),
			FSimpleDelegate::CreateLambda([CloseHandle] { CloseHandle->Dismiss(); }),
			VisibleState));

		if (TSharedPtr<SNotificationItem> Item = FSlateNotificationManager::Get().AddNotification(Info))
		{
			CloseHandle->Item = Item;
			Item->SetCompletionState(VisibleState);
		}
	}
}

void FPoseToolbar::Register()
{
	if (RegisteredExtender.IsBound())
	{
		return;
	}

	IAnimationEditorModule& Module = FModuleManager::LoadModuleChecked<IAnimationEditorModule>("AnimationEditor");

	// 模块返回的是普通 TArray<委托>，没有句柄；按值保留一份副本，退订时按值删除，
	// 这样别人在我们的槽位之前增删条目也不会牵连到我们。
	RegisteredExtender = IAnimationEditorModule::FAnimationEditorToolbarExtender::CreateStatic(
		&FPoseToolbar::ExtendToolbar);

	Module.GetAllAnimationEditorToolbarExtenders().Add(RegisteredExtender);
}

void FPoseToolbar::Unregister()
{
	if (!RegisteredExtender.IsBound())
	{
		return;
	}

	if (IAnimationEditorModule* Module = FModuleManager::GetModulePtr<IAnimationEditorModule>("AnimationEditor"))
	{
		// TDelegate 没有 operator==，按句柄比对定位我们的那一项。
		// 句柄唯一标识「哪个委托实例」，比下标稳：别人增删不会让我们删错。
		TArray<IAnimationEditorModule::FAnimationEditorToolbarExtender>& Extenders =
			Module->GetAllAnimationEditorToolbarExtenders();

		const FDelegateHandle OurHandle = RegisteredExtender.GetHandle();
		const int32 FoundIndex = Extenders.IndexOfByPredicate(
			[OurHandle](const IAnimationEditorModule::FAnimationEditorToolbarExtender& Extender)
			{
				return Extender.GetHandle() == OurHandle;
			});

		if (FoundIndex != INDEX_NONE)
		{
			Extenders.RemoveAt(FoundIndex);
		}
	}

	RegisteredExtender.Unbind();
}

TSharedRef<FExtender> FPoseToolbar::ExtendToolbar(
	const TSharedRef<FUICommandList> CommandList, TSharedRef<IAnimationEditor> AnimationEditor)
{
	TSharedRef<FExtender> Extender = MakeShared<FExtender>();
	TWeakPtr<IAnimationEditor> WeakEditor = AnimationEditor;

	Extender->AddToolBarExtension(
		"Asset",
		EExtensionHook::After,
		CommandList,
		FToolBarExtensionDelegate::CreateLambda(
			[WeakEditor](FToolBarBuilder& ToolbarBuilder)
			{
				ToolbarBuilder.AddToolBarButton(
					FUIAction(
						FExecuteAction::CreateStatic(&FPoseToolbar::OnExtractClicked, WeakEditor),
						FCanExecuteAction::CreateStatic(&FPoseToolbar::CanExtractClicked, WeakEditor)),
					NAME_None,
					LOCTEXT("ExtractPoseLabel", "抽取姿势代理"),
					LOCTEXT("ExtractPoseTooltip", "把当前暂停帧的全身姿态抽成一个姿势代理资产。"),
					FSlateIcon(FAppStyle::GetAppStyleSetName(), "Persona.CreateAsset"));
			}));

	return Extender;
}

bool FPoseToolbar::CanExtractClicked(TWeakPtr<IAnimationEditor> WeakAnimationEditor)
{
	IPersonaToolkit* Toolkit = ResolveToolkit(WeakAnimationEditor);
	if (Toolkit == nullptr)
	{
		return false;
	}

	FText Reason;
	return FPoseExtractor::CanExtract(*Toolkit, Reason);
}

void FPoseToolbar::OnExtractClicked(TWeakPtr<IAnimationEditor> WeakAnimationEditor)
{
	IPersonaToolkit* Toolkit = ResolveToolkit(WeakAnimationEditor);
	if (Toolkit == nullptr)
	{
		NotifyExtraction(LOCTEXT("NoEditor", "找不到当前动画编辑器的预览上下文。"), /*bSuccess*/ false);
		return;
	}

	FText Error;
	UPoseProxyAsset* NewAsset = FPoseExtractor::ExtractToNewAsset(*Toolkit, PoseOutputPath, Error);

	if (NewAsset == nullptr)
	{
		NotifyExtraction(Error, /*bSuccess*/ false);
		return;
	}

	NotifyExtraction(
		FText::Format(LOCTEXT("ExtractSucceeded", "已生成姿势代理：{0}"), FText::FromString(NewAsset->GetName())),
		/*bSuccess*/ true,
		TWeakObjectPtr<UPoseProxyAsset>(NewAsset));
}

#undef LOCTEXT_NAMESPACE
