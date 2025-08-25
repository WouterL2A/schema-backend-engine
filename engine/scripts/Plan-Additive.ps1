<# 
  PLAN (no writes). Prints a diff plan and exits the server.
  Usage:
    .\Plan-Additive.ps1
    .\Plan-Additive.ps1 -MetaPath C:\GitHub\Schema_Meta_System\meta\modelSchema.json -BindHost localhost -Port 8000
#>
[CmdletBinding()]
param(
  [string]$MetaPath,
  [string]$BindHost = "localhost",
  [int]$Port = 8000,
  [string]$LogPath = ".\migrate_plan.log"
)

$ErrorActionPreference = "Stop"

function Resolve-MetaPath {
  param([string]$Hint)
  if ($Hint -and (Test-Path $Hint)) { return (Resolve-Path $Hint).Path }
  if ($env:MODEL_META_PATH -and (Test-Path $env:MODEL_META_PATH)) { return (Resolve-Path $env:MODEL_META_PATH).Path }
  $defaultLocal = Join-Path $PSScriptRoot "..\..\schema\schema.meta.json"
  if (Test-Path $defaultLocal) { return (Resolve-Path $defaultLocal).Path }
  throw "Meta file not found. Provide -MetaPath or set MODEL_META_PATH."
}

$metaFile = Resolve-MetaPath -Hint $MetaPath
$env:MODEL_META_PATH = $metaFile

$hash = (Get-FileHash -Path $metaFile -Algorithm SHA256).Hash.ToLower()
$ack  = $hash.Substring(0,8)

$env:ENGINE_APPLY_ADDITIVE_PLAN = "1"
$env:LOG_LEVEL = "DEBUG"

Write-Host "== PLAN (no writes) ==" -ForegroundColor Cyan
Write-Host "Meta: $metaFile" -ForegroundColor DarkGray
Write-Host "ACK : $ack" -ForegroundColor DarkGray
Write-Host "Logs: $LogPath" -ForegroundColor DarkGray
Write-Host ""

$cmd = "uvicorn engine.main:app --host $BindHost --port $Port --log-level debug"
try {
  Invoke-Expression "$cmd 2>&1 | Tee-Object -FilePath '$LogPath'"
} finally {
  Remove-Item Env:\ENGINE_APPLY_ADDITIVE_PLAN -ErrorAction SilentlyContinue
}

Write-Host "`nPlan complete. See $LogPath" -ForegroundColor Green
