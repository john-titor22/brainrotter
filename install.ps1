<#
  Brainrotter installer (Windows).

  Run from the unzipped Brainrotter folder:
      Right-click  install.ps1  ->  Run with PowerShell
  or, in a terminal:
      powershell -ExecutionPolicy Bypass -File install.ps1

  Installs any missing prerequisites (Python, git, ffmpeg, Node, Ollama) with
  winget, builds the virtualenv, vendors the render engine, pulls the local
  model, and drops a "Brainrotter" shortcut on your Desktop.

  Flags (for automated / sandbox testing):
    -NonInteractive   don't wait for keypresses
    -SkipModel        don't `ollama pull` (the ~5 GB download)
#>
param(
  [switch]$NonInteractive,
  [switch]$SkipModel
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Step($m) { Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Have($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Pause-Exit($code) {
  if (-not $NonInteractive) { Read-Host "Press Enter to close" }
  exit $code
}

function Refresh-Path {
  $m = [Environment]::GetEnvironmentVariable("Path", "Machine")
  $u = [Environment]::GetEnvironmentVariable("Path", "User")
  $env:Path = "$m;$u"
}

function Winget-Install($id, $probe) {
  if ($probe -and (Have $probe)) { Write-Host "  $probe already installed"; return }
  Write-Host "  installing $id ..."
  winget install --id $id --accept-package-agreements --accept-source-agreements `
    --silent --disable-interactivity 2>&1 | Out-Null
  Refresh-Path
}

# --- 0. winget ------------------------------------------------------------
if (-not (Have "winget")) {
  Write-Host "winget is required (App Installer from the Microsoft Store)." -ForegroundColor Red
  Write-Host "Install it, then re-run this script."
  Pause-Exit 1
}

Step "1/6  prerequisites"
Winget-Install "Python.Python.3.12"  "python"
Winget-Install "Git.Git"             "git"
Winget-Install "Gyan.FFmpeg"         "ffmpeg"
Winget-Install "OpenJS.NodeJS.LTS"   "node"
Winget-Install "Ollama.Ollama"       "ollama"
Refresh-Path

# resolve a python launcher
$py = $null
foreach ($c in @("python", "python3", "py")) { if (Have $c) { $py = $c; break } }
if (-not $py) {
  # winget's user-scope python
  $cand = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python" -Filter python.exe -Recurse -EA SilentlyContinue |
          Select-Object -First 1
  if ($cand) { $py = $cand.FullName }
}
if (-not $py) {
  Write-Host "Python not found even after install. Open a NEW terminal and re-run." -ForegroundColor Red
  Pause-Exit 1
}

Step "2/6  virtualenv"
if (-not (Test-Path ".venv\Scripts\python.exe")) { & $py -m venv .venv }
$vpy = ".\.venv\Scripts\python.exe"

Step "3/6  Python packages  (a few minutes)"
& $vpy -m pip install --upgrade pip --quiet
& $vpy -m pip install -r requirements.txt
& $vpy -m pip install -e .

Step "4/6  render engine + config + shortcut"
& $vpy -m brainrotter.cli setup

Step "5/6  local model  (llama3.1:8b, ~5 GB download)"
if ($SkipModel) {
  Write-Host "  skipped (-SkipModel). Run later:  ollama pull llama3.1:8b"
} elseif (Have "ollama") {
  try { ollama pull llama3.1:8b } catch { Write-Host "  ollama pull failed - run 'ollama pull llama3.1:8b' later." }
} else {
  Write-Host "  Ollama not on PATH yet. Open a new terminal and run: ollama pull llama3.1:8b"
}

Step "6/6  check"
& $vpy -m brainrotter.cli doctor

Write-Host @"

--------------------------------------------------------------------
Brainrotter is installed.

One manual step for background footage from YouTube:
  1. In Chrome/Edge, install "Get cookies.txt LOCALLY"
  2. Open youtube.com signed in -> Export -> save as:
        $root\assets\cookies.txt
  3. Then:  .\.venv\Scripts\brainrotter footage sync

Open it: double-click the "Brainrotter" shortcut on your Desktop.
--------------------------------------------------------------------
"@ -ForegroundColor Green
if (-not $NonInteractive) { Read-Host "Press Enter to close" }
