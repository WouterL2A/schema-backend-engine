<# 
  Emergency one-off helper for SQLite only (adds a nullable column).
  Example:
    .\QuickFix-AddColumnSQLite.ps1 -DbPath .\app.db -Table workflow_state -Column process_definition_id -Type "VARCHAR(36)"
#>
[CmdletBinding()]
param(
  [string]$DbPath = ".\app.db",
  [Parameter(Mandatory)][string]$Table,
  [Parameter(Mandatory)][string]$Column,
  [string]$Type = "VARCHAR(36)"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $DbPath)) {
  throw "SQLite DB not found: $DbPath"
}

$sql = "ALTER TABLE $Table ADD COLUMN $Column $Type"
Write-Host "Running: $sql" -ForegroundColor Yellow

# Write Python to a temp file (PowerShell-safe)
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

Set-Content -Path $tmp -Value $py -Encoding UTF8
try {
  & python $tmp
} finally {
  Remove-Item $tmp -ErrorAction SilentlyContinue
}
