# Link MotionWeaver training outputs to the D: NVMe drive.
#
# Source data stays on E: (USB HDD, read-only use). Training views and weights
# live on D:. Code contains relative references such as training/runs/..., so a
# junction keeps them working without touching any source file.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File tools/install_drive_links.ps1 -WhatIf
#   powershell -ExecutionPolicy Bypass -File tools/install_drive_links.ps1
#
# Safety: never deletes a real directory that has content; conflicts are
# reported and skipped.

param(
    [switch]$WhatIf
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$dataRoot = 'D:\MotionWeaverData'

# relative link path -> folder under $dataRoot
#
# output/ is deliberately NOT linked: it holds only ~1.7 MB of historical notes
# and commit receipts, not training artifacts. Pointing it at D: would leave
# those 13 real entries stranded for no benefit.
$links = [ordered]@{
    'training\runs'     = 'training_runs'
    'model-weight\runs' = 'model_weight\runs'
}

if (-not (Test-Path $dataRoot)) {
    throw "Data root not found: $dataRoot"
}

foreach ($relative in $links.Keys) {
    $targetName = $links[$relative]
    $linkPath = Join-Path $repoRoot $relative
    $targetPath = Join-Path $dataRoot $targetName

    Write-Output "==> $relative -> $targetPath"

    if (-not (Test-Path $targetPath)) {
        if ($WhatIf) {
            Write-Output "    [WhatIf] would create target dir"
        } else {
            New-Item -ItemType Directory -Path $targetPath -Force | Out-Null
            Write-Output "    created target dir"
        }
    }

    if (Test-Path $linkPath) {
        $item = Get-Item $linkPath -Force
        if ($item.LinkType -eq 'Junction') {
            Write-Output "    already a junction, skip"
            continue
        }
        $children = @(Get-ChildItem $linkPath -Force -ErrorAction SilentlyContinue)
        if ($children.Count -gt 0) {
            Write-Output "    WARNING: real directory has $($children.Count) entries, skip: $linkPath"
            continue
        }
        if ($WhatIf) {
            Write-Output "    [WhatIf] empty dir, would replace with junction"
        } else {
            Remove-Item $linkPath -Force
            New-Item -ItemType Junction -Path $linkPath -Target $targetPath | Out-Null
            Write-Output "    replaced empty dir with junction"
        }
    } else {
        if ($WhatIf) {
            Write-Output "    [WhatIf] would create junction"
        } else {
            New-Item -ItemType Junction -Path $linkPath -Target $targetPath | Out-Null
            Write-Output "    created junction"
        }
    }
}

Write-Output ''
Write-Output '=== verify ==='
foreach ($relative in $links.Keys) {
    $linkPath = Join-Path $repoRoot $relative
    if (Test-Path $linkPath) {
        $item = Get-Item $linkPath -Force
        $target = $item.Target -join ','
        Write-Output ("{0,-22} LinkType={1} Target={2}" -f $relative, $item.LinkType, $target)
    } else {
        Write-Output ("{0,-22} missing" -f $relative)
    }
}
