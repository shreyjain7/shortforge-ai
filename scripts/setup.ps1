<#
.SYNOPSIS
  One-shot setup for ShortForge AI on Windows.

.DESCRIPTION
  Installs missing prerequisites with winget (FFmpeg, uv, Node.js, Ollama, Rust), creates the
  Python 3.12 environment, installs backend + frontend dependencies and builds the UI.
  Models are NOT downloaded here: the first-run wizard shows their sizes and asks first.

.PARAMETER SkipTauri
  Skip Rust / the native desktop build (the UI can still be used at http://127.0.0.1:8756).
#>
param([switch]$SkipTauri)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Have($cmd) { return [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function WingetInstall($id, $cmd) {
  if (Have $cmd) { Write-Host "  [ok] $cmd" -ForegroundColor Green; return }
  Write-Host "  installing $id ..." -ForegroundColor Cyan
  winget install --id $id -e --accept-package-agreements --accept-source-agreements --disable-interactivity | Out-Null
  $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH", "User")
}

Write-Host "`nShortForge AI setup" -ForegroundColor Magenta
Write-Host "Prerequisites:"
WingetInstall "Gyan.FFmpeg" "ffmpeg"
WingetInstall "astral-sh.uv" "uv"
WingetInstall "OpenJS.NodeJS.LTS" "node"
WingetInstall "Ollama.Ollama" "ollama"
if (-not $SkipTauri) { WingetInstall "Rustlang.Rustup" "cargo" }

if (-not (Have "uv")) {
  $uv = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\astral-sh.uv*\uv.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($uv) { $env:PATH = "$($uv.DirectoryName);$env:PATH" } else { throw "uv not found; open a new terminal and re-run." }
}

Write-Host "`nPython environment (3.12):"
uv python install 3.12
if (-not (Test-Path ".venv")) { uv venv --python 3.12 .venv }
$hasNvidia = [bool](Get-Command nvidia-smi -ErrorAction SilentlyContinue)
$extras = if ($hasNvidia) { ".[cuda,dev]" } else { ".[dev]" }
uv pip install --python .venv -e $extras
Write-Host "  [ok] backend installed ($extras)" -ForegroundColor Green

Write-Host "`nDesktop UI:"
Push-Location "apps\desktop"
npm install
npm run build
Pop-Location
Write-Host "  [ok] UI built" -ForegroundColor Green

if (-not $SkipTauri -and (Have "cargo")) {
  Write-Host "`nNative desktop app (first build takes a few minutes):"
  Push-Location "apps\desktop"
  npx tauri build --no-bundle
  Pop-Location
  Write-Host "  [ok] apps\desktop\src-tauri\target\release\shortforge.exe" -ForegroundColor Green
}

Write-Host "`nDone. Start ShortForge with scripts\start.ps1 (or run the native app)." -ForegroundColor Magenta
