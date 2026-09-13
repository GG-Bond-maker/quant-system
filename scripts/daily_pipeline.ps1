# AQP 每日盘后流水线（P1-1）Windows 版
# 用法: powershell -File scripts/daily_pipeline.ps1 [-Date 2026-08-28] [-Codes "600519,000001"] [-DryRun]
param(
    [string]$Date = "",
    [string]$Codes = "600519,000001,300750",
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { Write-Host "❌ 未找到后端 venv: $Py"; exit 1 }

$argv = @("-m", "app.data.pipeline", "--codes", $Codes)
if ($Date -ne "") { $argv += @("--date", $Date) }
if ($DryRun) { $argv += "--dry-run" }

Push-Location (Join-Path $Root "backend")
try { & $Py @argv } finally { Pop-Location }
exit $LASTEXITCODE
