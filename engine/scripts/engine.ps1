<# 
  One command for plan/apply/start/quickfix with logging and guardrails.

  Examples:
    .\engine.ps1 plan
    .\engine.ps1 apply -AllowNonEmpty
    .\engine.ps1 start
    .\engine.ps1 quickfix -Table workflow_state -Column process_definition_id -Type "VARCHAR(36)"
#>

[CmdletBinding(DefaultParameterSetName="Run")]
param(
  [Parameter(Mandatory, Position=0)]
  [ValidateSet("plan","apply","start","quickfix")]
  [string]$Action,

  # General
  [string]$MetaPath,
  [string]$BindHost = "localhost",
  [int]$BindPort = 8000,
  [string]$LogDir = ".\logs",

  # Apply interlocks (default false to satisfy analyzer)
  [switch]$AllowNonEmpty,
  [switch]$AllowRemote,

  # Quickfix (SQLite only)
  [string]$DbPath = ".\app.db",
  [string]$Table,
  [string]$Column,
  [string]$Type = "VARCHAR(36)"
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

function Get-Ack {
  param([Parameter(Mandatory)][string]$Path)
  $h = (Get-FileHash -Path $Path -Algorithm SHA256).Hash.ToLower()
  return $h.Substring(0,8)
}

function New-LogFile {
  param([string]$Prefix)
  if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
  $ts = Get-Date -Format "yyyyMMdd-HHmmss"
  return (Join-Path $LogDir "$Prefix-$ts.log")
}

function Run-WithTee {
  <#
    Runs a command line *with a pipeline* reliably by spawning a child PowerShell:
      "$Cmd 2>&1 | Tee-Object -FilePath $Log"
    Shows live output in the console and writes the same to $Log.
    Returns the child exit code.
  #>
  param(
    [Parameter(Mandatory)][string]$Cmd,
    [Parameter(Mandatory)][string]$LogPath
  )
  $psCmd = "$Cmd 2>&1 | Tee-Object -FilePath `"$LogPath`""
  $p = Start-Process -FilePath "powershell" `
                     -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-Command", $psCmd) `
                     -NoNewWindow -Wait -PassThru
  return ($p.ExitCode)
}

function Invoke-Uvicorn {
  param(
    [hashtable]$EnvVars,
    [string]$ActionName,
    [string]$LogPrefix = "engine"
  )
  $log = New-LogFile -Prefix "$LogPrefix-$ActionName"
  $cmd = "uvicorn engine.main:app --host $BindHost --port $BindPort --log-level debug"

  Write-Host "== $($ActionName.ToUpper()) ==" -ForegroundColor Cyan
  if ($EnvVars.ContainsKey("MODEL_META_PATH")) {
    Write-Host "Meta: $($EnvVars["MODEL_META_PATH"])" -ForegroundColor DarkGray
  }
  if ($EnvVars.ContainsKey("ENGINE_APPLY_ADDITIVE_ACK")) {
    Write-Host "ACK : $($EnvVars["ENGINE_APPLY_ADDITIVE_ACK"])" -ForegroundColor DarkGray
  }
  Write-Host "Logs: $log" -ForegroundColor DarkGray
  Write-Host ""

  # apply env vars
  $applied = @()
  foreach ($k in $EnvVars.Keys) {
    Set-Item -Path "Env:$k" -Value $EnvVars[$k]
    $applied += $k
  }

  $exitCode = 0
  try {
    $exitCode = Run-WithTee -Cmd $cmd -LogPath $log
  } catch {
    $exitCode = 1
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
  } finally {
    foreach ($k in $applied) {
      if ($k -notin @("MODEL_META_PATH")) {
        Remove-Item "Env:$k" -ErrorAction SilentlyContinue
      }
    }
    Write-Host "`nDone. See log: $log" -ForegroundColor Green
  }

  if ($exitCode -ne 0) {
    $ans = Read-Host "Command failed. Open log in Notepad now? (y/N)"
    if ($ans -match '^(y|yes)$') { Start-Process notepad.exe $log }
    exit $exitCode
  }
}

switch ($Action) {

  "plan" {
    $meta = Resolve-MetaPath -Hint $MetaPath
    $ack  = Get-Ack -Path $meta
    $envs = @{
      "MODEL_META_PATH" = $meta
      "ENGINE_APPLY_ADDITIVE_PLAN" = "1"
      "LOG_LEVEL" = "DEBUG"
    }
    Invoke-Uvicorn -EnvVars $envs -ActionName "plan"
    break
  }

  "apply" {
    $meta = Resolve-MetaPath -Hint $MetaPath
    $ack  = Get-Ack -Path $meta
    $envs = @{
      "MODEL_META_PATH"             = $meta
      "ENGINE_APPLY_ADDITIVE"       = "1"
      "ENGINE_APPLY_ADDITIVE_ACK"   = $ack
      "LOG_LEVEL"                   = "DEBUG"
    }
    if ($AllowNonEmpty) { $envs["ENGINE_ALLOW_NONEMPTY"] = "1" }
    if ($AllowRemote)   { $envs["ENGINE_ALLOW_REMOTE"]   = "1" }
    Invoke-Uvicorn -EnvVars $envs -ActionName "apply"
    break
  }

  "start" {
    $envs = @{}
    if ($MetaPath -or $env:MODEL_META_PATH) {
      $envs["MODEL_META_PATH"] = Resolve-MetaPath -Hint $MetaPath
    }
    Invoke-Uvicorn -EnvVars $envs -ActionName "start" -LogPrefix "run"
    break
  }

  "quickfix" {
    if (-not (Test-Path $DbPath)) { throw "SQLite DB not found: $DbPath" }
    if (-not $Table)  { throw "-Table is required for quickfix." }
    if (-not $Column) { throw "-Column is required for quickfix." }

    $sql = "ALTER TABLE $Table ADD COLUMN $Column $Type"
    Write-Host "SQLite QUICKFIX: $sql" -ForegroundColor Yellow

    # temp Python file (no heredoc/redirection)
    $tmp = New-TemporaryFile
    $py = @'
from sqlalchemy import create_engine
import os
db = r"sqlite:///" + os.path.abspath(r"{DBPATH}")
engine = create_engine(db)
sql = """{SQL}"""
with engine.begin() as c:
    try:
        c.exec_driver_sql(sql)
        print("Added:", sql)
    except Exception as e:
        print("No change / already exists:", e)
'@.Replace("{DBPATH}", (Resolve-Path $DbPath).Path).Replace("{SQL}", $sql)

    try {
      Set-Content -Path $tmp -Value $py -Encoding UTF8
      & python $tmp
    } finally {
      Remove-Item $tmp -ErrorAction SilentlyContinue
    }
    break
  }
}
