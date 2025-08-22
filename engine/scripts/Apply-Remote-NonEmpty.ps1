#Requires -Version 5.1
param(
  [Parameter(Mandatory=$true)][string]$DbUrl,
  [string]$MetaPath = "C:\GitHub\Schema_Meta_System\meta\modelSchema.json",
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

Write-Host "⚠️ APPLY additive changes on REMOTE & NON-EMPTY DB."
Write-Host "This sets ENGINE_ALLOW_REMOTE=1 and ENGINE_ALLOW_NONEMPTY=1"
Write-Host "Meta SHA256: $hash  (ACK: $ack)"
Write-Host "DB URL: $DbUrl"

$oldMeta = $env:MODEL_META_PATH
$oldDB   = $env:DATABASE_URL
$oldApply= $env:ENGINE_APPLY_ADDITIVE
$oldAck  = $env:ENGINE_APPLY_ADDITIVE_ACK
$oldRem  = $env:ENGINE_ALLOW_REMOTE
$oldNonE = $env:ENGINE_ALLOW_NONEMPTY

$env:MODEL_META_PATH = $MetaPath
$env:DATABASE_URL = $DbUrl
$env:ENGINE_APPLY_ADDITIVE = "1"
$env:ENGINE_APPLY_ADDITIVE_ACK = $ack
$env:ENGINE_ALLOW_REMOTE = "1"
$env:ENGINE_ALLOW_NONEMPTY = "1"

try {
  uvicorn engine.main:app --host 127.0.0.1 --port $Port --log-level info
}
finally {
  $env:ENGINE_ALLOW_NONEMPTY   = $oldNonE
  $env:ENGINE_ALLOW_REMOTE     = $oldRem
  $env:ENGINE_APPLY_ADDITIVE   = $oldApply
  $env:ENGINE_APPLY_ADDITIVE_ACK = $oldAck
  $env:DATABASE_URL = $oldDB
  $env:MODEL_META_PATH = $oldMeta
}
