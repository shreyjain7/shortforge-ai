<#
.SYNOPSIS
  Development mode: engine with auto-reload + Vite dev server (+ Tauri window with -Tauri).
#>
param([switch]$Tauri)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root ".venv\Scripts\python.exe"
Start-Process -FilePath $py -ArgumentList "-m", "uvicorn", "shortforge.server:app", "--port", "8756", "--reload", "--reload-dir", "backend" -WorkingDirectory $Root
Push-Location (Join-Path $Root "apps\desktop")
if ($Tauri) { npx tauri dev } else { npm run dev }
Pop-Location
