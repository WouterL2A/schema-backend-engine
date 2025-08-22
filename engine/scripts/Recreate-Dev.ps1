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

Write-Host "⚠️ DEV ONLY: dropping ALL tables, then create_all()."
Write-Host "DB URL: $DbUrl"

$oldMeta = $env:MODEL_META_PATH
$oldDB   = $env:DATABASE_URL
$oldRec  = $env:ENGINE_RECREATE

$env:MODEL_META_PATH = $MetaPath
$env:DATABASE_URL = $DbUrl
$env:ENGINE_RECREATE = "1"

try {
  uvicorn engine.main:app --host 127.0.0.1 --port $Port --log-level info
}
finally {
  $env:ENGINE_RECREATE = $oldRec
  $env:DATABASE_URL = $oldDB
  $env:MODEL_META_PATH = $oldMeta
}
