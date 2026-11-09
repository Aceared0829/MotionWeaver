// Copyright ZhaoZining. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "Logging/LogMacros.h"

/**
 * 插件统一日志分类。
 * 必须导出：编辑器模块也要用同一个分类打日志，
 * 不导出会在链接期报 LogMotionWeaver 未解析。
 */
MOTIONWEAVER_API DECLARE_LOG_CATEGORY_EXTERN(LogMotionWeaver, Log, All);
