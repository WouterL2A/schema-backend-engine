#Requires -Version 5.1
param(
  [string]$MetaPath = "C:\GitHub\Schema_Meta_System\meta\modelSchema.json",
  [string]$DbUrl   = "sqlite:///./app.db",
  [int]$Port       = 8000
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Test-Path -Path $MetaPath)) {
  Write-Error "Meta file not found: $MetaPath"
  exit 1
}

$hash = (Get-FileHash -Path $MetaPath -Algorithm SHA256).Hash.ToLower()
$ack  = $hash.Substring(0,8)

Write-Host "Planning additive changes only (no writes)."
Write-Host "Meta SHA256: $hash  (ACK hint: $ack)"
Write-Host "DB URL: $DbUrl"

# Set env flags
$oldMeta = $env:MODEL_META_PATH
$oldDB   = $env:DATABASE_URL
$oldPlan = $env:ENGINE_APPLY_ADDITIVE_PLAN

$env:MODEL_META_PATH = $MetaPath
$env:DATABASE_URL = $DbUrl
$env:ENGINE_APPLY_ADDITIVE_PLAN = "1"

try {
  uvicorn engine.main:app --host 127.0.0.1 --port $Port --log-level info
}
finally {
  $env:ENGINE_APPLY_ADDITIVE_PLAN = $oldPlan
  $env:DATABASE_URL = $oldDB
  $env:MODEL_META_PATH = $oldMeta
}
