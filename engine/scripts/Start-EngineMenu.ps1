#Requires -Version 5.1
<#
  Interactive launcher for the Schema Backend Engine with safety interlocks.

  Scenarios:
    1) PLAN (local) - no writes
    2) APPLY (local, EMPTY DB) - additive only
    3) APPLY (local, NON-EMPTY DB) - additive only (risky)
    4) PLAN (REMOTE DB) - no writes
    5) APPLY (REMOTE, NON-EMPTY) - additive only (max interlocks)
    6) DEV: Drop & Recreate - dev only
#>

[CmdletBinding()]
param(
  [string]$DefaultMetaPath    = "C:\GitHub\Schema_Meta_System\meta\modelSchema.json",
  [string]$DefaultLocalDbUrl  = "sqlite:///./app.db",
  [string]$DefaultHost        = "127.0.0.1",
  [int]   $DefaultPort        = 8000
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-Uvicorn {
  try { $null = & uvicorn --version 2>$null; return $true } catch { return $false }
}

function Get-Ack {
  param([string]$MetaPath)
  if (-not (Test-Path -Path $MetaPath)) { throw "Meta file not found: $MetaPath" }
  $hash = (Get-FileHash -Path $MetaPath -Algorithm SHA256).Hash.ToLower()
  return @{ Hash = $hash; Ack = $hash.Substring(0,8) }
}

function Invoke-Engine {
  param(
    [hashtable]$EnvVars,
    [string]$BindHost = $DefaultHost,
    [int]$Port        = $DefaultPort
  )
  # backup and set env vars using Env: provider
  $backup = @{}
  foreach ($k in $EnvVars.Keys) {
    $existing = Get-Item -Path ("Env:{0}" -f $k) -ErrorAction SilentlyContinue
    $backup[$k] = if ($existing) { $existing.Value } else { $null }
    Set-Item -Path ("Env:{0}" -f $k) -Value $EnvVars[$k]
  }

  try {
    Write-Host "`nLaunching server with:" -ForegroundColor Cyan
    $envDump = ($EnvVars.GetEnumerator() | Sort-Object Name | ForEach-Object { "  $($_.Name)=$($_.Value)" }) -join "`n"
    Write-Host $envDump
    Write-Host "Command: uvicorn engine.main:app --host $BindHost --port $Port --log-level info" -ForegroundColor DarkCyan
    & uvicorn engine.main:app --host $BindHost --port $Port --log-level info
  }
  finally {
    # restore env
    foreach ($k in $EnvVars.Keys) {
      if ($backup.ContainsKey($k) -and $null -ne $backup[$k]) {
        Set-Item -Path ("Env:{0}" -f $k) -Value $backup[$k]
      } else {
        Remove-Item -Path ("Env:{0}" -f $k) -ErrorAction SilentlyContinue
      }
    }
  }
}

function Confirm-Action {
  param([string]$Message)
  $ans = Read-Host "$Message [y/N]"
  return ($ans.Trim().ToLower() -eq 'y')
}

function Read-NonEmpty {
  param([string]$Prompt, [string]$Default = "")
  while ($true) {
    $v = Read-Host ("$Prompt" + ($(if ($Default) {" [$Default]"} else {""})))
    if (-not $v -and $Default) { return $Default }
    if ($v) { return $v }
    Write-Host "Please enter a value." -ForegroundColor Yellow
  }
}

# Ensure uvicorn exists
if (-not (Test-Uvicorn)) {
  Write-Host "uvicorn not found on PATH. Install with:  pip install uvicorn[standard] fastapi sqlalchemy" -ForegroundColor Yellow
  throw "Missing dependency: uvicorn"
}

# Repo root: this script is at engine\scripts, so go two levels up
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Push-Location $RepoRoot

try {
  while ($true) {
    Clear-Host
    Write-Host "=== Schema Backend Engine Launcher ===" -ForegroundColor Cyan
    Write-Host "Meta default: $DefaultMetaPath"
    Write-Host "Local DB default: $DefaultLocalDbUrl"
    Write-Host "Server: http://$DefaultHost`:$DefaultPort"
    Write-Host ""
    Write-Host "1) PLAN (local) - no writes" -ForegroundColor Green
    Write-Host "2) APPLY (local, EMPTY DB) - additive only" -ForegroundColor Green
    Write-Host "3) APPLY (local, NON-EMPTY DB) - additive only  [RISK]" -ForegroundColor Yellow
    Write-Host "4) PLAN (REMOTE DB) - no writes" -ForegroundColor Green
    Write-Host "5) APPLY (REMOTE, NON-EMPTY) - additive only  [MAX RISK]" -ForegroundColor Red
    Write-Host "6) DEV: Drop & Recreate" -ForegroundColor Red
    Write-Host "Q) Quit"
    Write-Host ""

    $choice = Read-Host "Choose an option"
    switch ($choice.Trim().ToUpper()) {
      '1' {
        $meta = Read-NonEmpty -Prompt "Meta path" -Default $DefaultMetaPath
        $db   = Read-NonEmpty -Prompt "DB URL"   -Default $DefaultLocalDbUrl
        $ackInfo = Get-Ack -MetaPath $meta
        Write-Host "`nPLAN ONLY: No database writes will occur." -ForegroundColor Green
        Write-Host "Meta SHA256: $($ackInfo.Hash)  (ACK hint: $($ackInfo.Ack))"
        if (-not (Confirm-Action "Proceed to start server in PLAN mode?")) { continue }
        Invoke-Engine -EnvVars @{
          MODEL_META_PATH = $meta
          DATABASE_URL = $db
          ENGINE_APPLY_ADDITIVE_PLAN = "1"
        }
      }
      '2' {
        $meta = Read-NonEmpty -Prompt "Meta path" -Default $DefaultMetaPath
        $db   = Read-NonEmpty -Prompt "DB URL"   -Default $DefaultLocalDbUrl
        $ackInfo = Get-Ack -MetaPath $meta
        Write-Host "`nAPPLY on EMPTY local DB (engine will refuse if schema is non-empty)." -ForegroundColor Yellow
        Write-Host "Meta SHA256: $($ackInfo.Hash)  (ACK: $($ackInfo.Ack))"
        if (-not (Confirm-Action "Type 'y' to APPLY additive changes")) { continue }
        Invoke-Engine -EnvVars @{
          MODEL_META_PATH = $meta
          DATABASE_URL = $db
          ENGINE_APPLY_ADDITIVE = "1"
          ENGINE_APPLY_ADDITIVE_ACK = $ackInfo.Ack
        }
      }
      '3' {
        $meta = Read-NonEmpty -Prompt "Meta path" -Default $DefaultMetaPath
        $db   = Read-NonEmpty -Prompt "DB URL"   -Default $DefaultLocalDbUrl
        $ackInfo = Get-Ack -MetaPath $meta
        Write-Host "`nAPPLY on NON-EMPTY local DB (adds columns/FKs). Ensure backups." -ForegroundColor Red
        Write-Host "Meta SHA256: $($ackInfo.Hash)  (ACK: $($ackInfo.Ack))"
        if (-not (Confirm-Action "Confirm you have a backup and want to APPLY")) { continue }
        Invoke-Engine -EnvVars @{
          MODEL_META_PATH = $meta
          DATABASE_URL = $db
          ENGINE_APPLY_ADDITIVE = "1"
          ENGINE_APPLY_ADDITIVE_ACK = $ackInfo.Ack
          ENGINE_ALLOW_NONEMPTY = "1"
        }
      }
      '4' {
        $meta = Read-NonEmpty -Prompt "Meta path" -Default $DefaultMetaPath
        $db   = Read-NonEmpty -Prompt "REMOTE DB URL (e.g. postgresql+psycopg2://user:pass@host/db)"
        $ackInfo = Get-Ack -MetaPath $meta
        Write-Host "`nPLAN on REMOTE DB - no writes." -ForegroundColor Green
        Write-Host "Meta SHA256: $($ackInfo.Hash)  (ACK hint: $($ackInfo.Ack))"
        if (-not (Confirm-Action "Proceed to PLAN against remote?")) { continue }
        Invoke-Engine -EnvVars @{
          MODEL_META_PATH = $meta
          DATABASE_URL = $db
          ENGINE_APPLY_ADDITIVE_PLAN = "1"
        }
      }
      '5' {
        $meta = Read-NonEmpty -Prompt "Meta path" -Default $DefaultMetaPath
        $db   = Read-NonEmpty -Prompt "REMOTE DB URL (e.g. postgresql+psycopg2://user:pass@host/db)"
        $ackInfo = Get-Ack -MetaPath $meta
        Write-Host "`nAPPLY on REMOTE & NON-EMPTY DB - maximum interlocks. Back up first." -ForegroundColor Red
        Write-Host "Meta SHA256: $($ackInfo.Hash)  (ACK: $($ackInfo.Ack))"
        if (-not (Confirm-Action "Confirm you understand the risk and want to APPLY")) { continue }
        Invoke-Engine -EnvVars @{
          MODEL_META_PATH = $meta
          DATABASE_URL = $db
          ENGINE_APPLY_ADDITIVE = "1"
          ENGINE_APPLY_ADDITIVE_ACK = $ackInfo.Ack
          ENGINE_ALLOW_REMOTE = "1"
          ENGINE_ALLOW_NONEMPTY = "1"
        }
      }
      '6' {
        $meta = Read-NonEmpty -Prompt "Meta path" -Default $DefaultMetaPath
        $db   = Read-NonEmpty -Prompt "DB URL"   -Default $DefaultLocalDbUrl
        Write-Host "`nDEV ONLY: This will DROP ALL tables then recreate." -ForegroundColor Red
        if (-not (Confirm-Action "Type 'y' to DROP ALL and recreate (dev only)")) { continue }
        Invoke-Engine -EnvVars @{
          MODEL_META_PATH = $meta
          DATABASE_URL = $db
          ENGINE_RECREATE = "1"
        }
      }
      'Q' { break }
      default {
        Write-Host "Unknown choice. Please select 1-6 or Q." -ForegroundColor Yellow
        Start-Sleep -Seconds 1
      }
    }

    Write-Host "`nServer stopped. Returning to menu..." -ForegroundColor DarkGray
    Start-Sleep -Seconds 1
  }
}
finally {
  Pop-Location
}
