# Unreal 集成

本目录只保留 MotionWeaver 插件镜像与同步工具。

此前这里的 `AIAnimation/`、`AIAnimationMotionMatching/`、`AILocomotionSystem/`、`python/`、`MotionWeaverCMCPreview/` 已于 2026-11 移除：旧推理插件是 79 骨 / 30 fps / 单输入 VQ 重建，与当前 refpose v3 的 78 骨 SOMA / 120 fps / 四输入契约不兼容；`AILocomotionDataset` 的 79 骨 UEFN JSON 产出也没有下游消费者；`python/` 的 UE 内 IK Retargeter 脚本依赖的上游数据盘已不存在。需要查阅这些代码时从 git 历史取回（`git show e7330af:unreal-script/AIAnimation/...`）。

## MotionWeaver 插件镜像

MotionWeaver 的唯一开发源为 `unreal-sample/AIMotionSample/Plugins/MotionWeaver/`。该插件已从本目录复制到新宿主并在 `AIMotionSample.uproject` 中启用；`unreal-script/MotionWeaver/` 是面向其他 UE 宿主和集成流程的同步镜像，日常开发直接修改宿主内的插件，不要在镜像目录单独修复代码。

上一代宿主 `UMWSamplePreview` 已于 2026-11 移除，其插件开发源随之消失；`Tools/Sync-MotionWeaver.ps1` 的 `$sourceRoot` 已改指新宿主。

在合并包含 Sample 插件变更的分支时，运行以下命令把源插件同步到镜像，然后将同步结果作为同一批次提交：

```powershell
& .\unreal-script\Tools\Sync-MotionWeaver.ps1
& .\unreal-script\Tools\Sync-MotionWeaver.ps1 -Check
```

同步脚本只复制插件源码和元数据，排除 `Content/` 和 `Binaries/`、`Intermediate/`、`Saved/` 等 UE 生成物，并删除镜像中已从源插件移除的文件。它拒绝通过非排除目录中的 Junction 或其他链接读写、删除文件；保留排除目录和空目录。`-Check` 返回非零状态时，说明合并前镜像尚未同步。
