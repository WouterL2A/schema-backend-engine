<# 
  Normal start (no plan/apply). Good for day-to-day dev, with logging.
  Usage:
    .\Start-Engine.ps1
    .\Start-Engine.ps1 -BindHost localhost -Port 8000 -LogPath .\engine_run.log
#>
[CmdletBinding()]
param(
  [string]$BindHost = "localhost",
  [int]$Port = 8000,
  [string]$LogPath = ".\engine_run.log"
)

$ErrorActionPreference = "Stop"

Write-Host "== START ENGINE ==" -ForegroundColor Cyan
Write-Host "Logs: $LogPath" -ForegroundColor DarkGray
Write-Host ""

$cmd = "uvicorn engine.main:app --host $BindHost --port $Port --log-level info"
try {
  Invoke-Expression "$cmd 2>&1 | Tee-Object -FilePath '$LogPath'"
} finally {
  Write-Host "`nStopped. See $LogPath" -ForegroundColor Green
}
