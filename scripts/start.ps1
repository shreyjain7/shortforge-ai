<#
.SYNOPSIS
  Start ShortForge AI: launches the native app if it was built, otherwise the engine + UI in your browser.
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $Root "apps\desktop\src-tauri\target\release\shortforge.exe"
if (Test-Path $exe) {
  Start-Process $exe
  exit 0
}
$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Run scripts\setup.ps1 first." }
Start-Process -WindowStyle Hidden -FilePath $py -ArgumentList "-m", "shortforge.server" -WorkingDirectory $Root
Start-Sleep -Seconds 4
Start-Process "http://127.0.0.1:8756"
