[CmdletBinding(SupportsShouldProcess)]
param(
	[switch]$Check
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$sourceRoot = Join-Path $repositoryRoot 'unreal-sample\AIMotionSample\Plugins\MotionWeaver'
$mirrorRoot = Join-Path $repositoryRoot 'unreal-script\MotionWeaver'
$excludedDirectories = @('Binaries', 'Intermediate', 'Saved', 'Content')

if (-not (Test-Path -LiteralPath $sourceRoot -PathType Container)) {
	throw "MotionWeaver 源插件不存在：$sourceRoot"
}

function Get-RelativePath([string]$Root, [string]$Path)
{
	$prefix = $Root.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
	if (-not $Path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
		throw "路径超出插件目录：$Path"
	}
	return $Path.Substring($Root.Length).TrimStart('\', '/')
}

function Test-IsExcludedPath([string]$RelativePath)
{
	$segments = $RelativePath -split '[\\/]'
	return [bool]($segments | Where-Object { $excludedDirectories -contains $_ })
}

function Assert-NoReparseParents([string]$Path)
{
	$current = [IO.Path]::GetFullPath($Path)
	while ($current -ne $repositoryRoot) {
		if (-not $current.StartsWith($repositoryRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
			throw "路径超出仓库：$current"
		}
		if (Test-Path -LiteralPath $current) {
			$item = Get-Item -LiteralPath $current -Force
			if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
				throw "同步目录包含链接，拒绝读写或删除：$current"
			}
		}
		$current = Split-Path -Parent $current
	}
}

function Get-PluginFiles([string]$Root)
{
	Assert-NoReparseParents $Root
	if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
		return
	}
	$pending = [Collections.Generic.Stack[string]]::new()
	$pending.Push($Root)
	while ($pending.Count -gt 0) {
		foreach ($item in Get-ChildItem -LiteralPath $pending.Pop() -Force) {
			$relativePath = Get-RelativePath $Root $item.FullName
			if (Test-IsExcludedPath $relativePath) {
				continue
			}
			Assert-NoReparseParents $item.FullName
			if ($item.PSIsContainer) {
				$pending.Push($item.FullName)
			} else {
				$item
			}
		}
	}
}

$sourceFiles = @(Get-PluginFiles $sourceRoot)

$sourceByRelativePath = @{}
foreach ($sourceFile in $sourceFiles) {
	$relativePath = Get-RelativePath $sourceRoot $sourceFile.FullName
	$sourceByRelativePath[$relativePath] = $sourceFile
}

$mirrorFiles = @()
if (Test-Path -LiteralPath $mirrorRoot -PathType Container) {
	$mirrorFiles = @(Get-PluginFiles $mirrorRoot)
}
Assert-NoReparseParents $mirrorRoot

$mismatches = [System.Collections.Generic.List[string]]::new()
foreach ($sourceFile in $sourceFiles) {
	$relativePath = Get-RelativePath $sourceRoot $sourceFile.FullName
	$mirrorPath = Join-Path $mirrorRoot $relativePath
	Assert-NoReparseParents $mirrorPath
	if (-not (Test-Path -LiteralPath $mirrorPath -PathType Leaf)) {
		$mismatches.Add("缺少 $relativePath")
		continue
	}

	$sourceHash = (Get-FileHash -LiteralPath $sourceFile.FullName -Algorithm SHA256).Hash
	$mirrorHash = (Get-FileHash -LiteralPath $mirrorPath -Algorithm SHA256).Hash
	if ($sourceHash -ne $mirrorHash) {
		$mismatches.Add("内容不同 $relativePath")
	}
}

foreach ($mirrorFile in $mirrorFiles) {
	$relativePath = Get-RelativePath $mirrorRoot $mirrorFile.FullName
	if (-not $sourceByRelativePath.ContainsKey($relativePath)) {
		$mismatches.Add("镜像多出 $relativePath")
	}
}

if ($Check) {
	if ($mismatches.Count -gt 0) {
		$mismatches | ForEach-Object { Write-Error $_ }
		throw 'MotionWeaver 镜像未同步。'
	}

	Write-Output "MotionWeaver 镜像已同步：$mirrorRoot"
	return
}

foreach ($sourceFile in $sourceFiles) {
	$relativePath = Get-RelativePath $sourceRoot $sourceFile.FullName
	$mirrorPath = Join-Path $mirrorRoot $relativePath
	Assert-NoReparseParents $mirrorPath
	$mirrorDirectory = Split-Path -Parent $mirrorPath
	if (-not (Test-Path -LiteralPath $mirrorDirectory -PathType Container)) {
		if ($PSCmdlet.ShouldProcess($mirrorDirectory, '创建镜像目录')) {
			New-Item -ItemType Directory -Path $mirrorDirectory -Force | Out-Null
		}
	}

	if ($PSCmdlet.ShouldProcess($mirrorPath, '从 Sample 插件复制')) {
		Copy-Item -LiteralPath $sourceFile.FullName -Destination $mirrorPath -Force
	}
}

foreach ($mirrorFile in $mirrorFiles) {
	$relativePath = Get-RelativePath $mirrorRoot $mirrorFile.FullName
	Assert-NoReparseParents $mirrorFile.FullName
	if (-not $sourceByRelativePath.ContainsKey($relativePath) -and
		$PSCmdlet.ShouldProcess($mirrorFile.FullName, '删除过期镜像文件')) {
		Remove-Item -LiteralPath $mirrorFile.FullName -Force
	}
}

Write-Output "MotionWeaver 已从 Sample 同步到：$mirrorRoot"
