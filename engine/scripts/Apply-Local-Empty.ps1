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

Write-Host "APPLY additive changes on an EMPTY local DB (creates tables, adds columns/FKs)."
Write-Host "Meta SHA256: $hash  (ACK: $ack)"
Write-Host "DB URL: $DbUrl"

$oldMeta = $env:MODEL_META_PATH
$oldDB   = $env:DATABASE_URL
$oldApply= $env:ENGINE_APPLY_ADDITIVE
$oldAck  = $env:ENGINE_APPLY_ADDITIVE_ACK
$oldPlan = $env:ENGINE_APPLY_ADDITIVE_PLAN
$oldNonE = $env:ENGINE_ALLOW_NONEMPTY

$env:MODEL_META_PATH = $MetaPath
$env:DATABASE_URL = $DbUrl
$env:ENGINE_APPLY_ADDITIVE = "1"
$env:ENGINE_APPLY_ADDITIVE_ACK = $ack
# ENGINE_ALLOW_NONEMPTY not set → engine will refuse if schema isn’t empty.

try {
  uvicorn engine.main:app --host 127.0.0.1 --port $Port --log-level info
}
finally {
  $env:ENGINE_ALLOW_NONEMPTY   = $oldNonE
  $env:ENGINE_APPLY_ADDITIVE   = $oldApply
  $env:ENGINE_APPLY_ADDITIVE_ACK = $oldAck
  $env:ENGINE_APPLY_ADDITIVE_PLAN = $oldPlan
  $env:DATABASE_URL = $oldDB
  $env:MODEL_META_PATH = $oldMeta
}
