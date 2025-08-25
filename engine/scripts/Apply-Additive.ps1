<# 
  APPLY additive schema changes, then start the server.
  Usage:
    .\Apply-Additive.ps1
    .\Apply-Additive.ps1 -MetaPath C:\GitHub\Schema_Meta_System\meta\modelSchema.json -AllowNonEmpty
#>
[CmdletBinding()]
param(
  [string]$MetaPath,
  [string]$BindHost = "localhost",
  [int]$Port = 8000,
  [switch]$AllowNonEmpty,  # default: $false (pass -AllowNonEmpty to enable)
  [switch]$AllowRemote,    # default: $false (pass -AllowRemote to enable)
  [string]$LogPath = ".\migrate_apply.log"
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

$env:ENGINE_APPLY_ADDITIVE = "1"
$env:ENGINE_APPLY_ADDITIVE_ACK = $ack
if ($AllowNonEmpty) { $env:ENGINE_ALLOW_NONEMPTY = "1" } else { Remove-Item Env:\ENGINE_ALLOW_NONEMPTY -ErrorAction SilentlyContinue }
if ($AllowRemote)   { $env:ENGINE_ALLOW_REMOTE   = "1" } else { Remove-Item Env:\ENGINE_ALLOW_REMOTE   -ErrorAction SilentlyContinue }
$env:LOG_LEVEL = "DEBUG"

Write-Host "== APPLY (additive) ==" -ForegroundColor Yellow
Write-Host "Meta: $metaFile" -ForegroundColor DarkGray
Write-Host "ACK : $ack" -ForegroundColor DarkGray
Write-Host "Logs: $LogPath" -ForegroundColor DarkGray
Write-Host ""

$cmd = "uvicorn engine.main:app --host $BindHost --port $Port --log-level debug"
$exitCode = 0
try {
  Invoke-Expression "$cmd 2>&1 | Tee-Object -FilePath '$LogPath'"
  $exitCode = $LASTEXITCODE
} catch {
  $exitCode = 1
  Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
} finally {
  Remove-Item Env:\ENGINE_APPLY_ADDITIVE -ErrorAction SilentlyContinue
  Remove-Item Env:\ENGINE_APPLY_ADDITIVE_ACK -ErrorAction SilentlyContinue
}

if ($exitCode -ne 0) {
  Write-Host "`nApply failed. Open the log for details: $LogPath" -ForegroundColor Red
  $ans = Read-Host "Open log in Notepad now? (y/N)"
  if ($ans -match '^(y|yes)$') { Start-Process notepad.exe $LogPath }
  exit $exitCode
}

Write-Host "`nApply completed. Server is running. Logs: $LogPath" -ForegroundColor Green
