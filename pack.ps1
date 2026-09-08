<#
  Build a shareable installer bundle: dist\Brainrotter-Setup.zip

  Contains every tracked file (code + vendored engine source + install.ps1),
  nothing else (.venv, workspace, cache, cookies are all excluded). The person
  you send it to unzips it and runs install.ps1.
#>

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (git status --porcelain) {
  Write-Host "You have uncommitted changes - commit them first so the bundle matches your repo." -ForegroundColor Yellow
}

New-Item -ItemType Directory -Force dist | Out-Null
$zip = "dist\Brainrotter-Setup.zip"
Remove-Item $zip -ErrorAction SilentlyContinue

git archive --format=zip --prefix=Brainrotter/ -o $zip HEAD

$size = "{0:N1} MB" -f ((Get-Item $zip).Length / 1MB)
Write-Host "`nBuilt $zip  ($size)" -ForegroundColor Green
Write-Host @"

Share it:
  1. Upload $zip anywhere (Google Drive, WeTransfer, Discord, a USB stick).
     Or attach it to a GitHub Release:  gh release create v0.1 $zip
  2. The other person:
       - downloads and unzips it
       - opens the Brainrotter folder
       - right-clicks install.ps1  ->  Run with PowerShell
         (or: powershell -ExecutionPolicy Bypass -File install.ps1)

install.ps1 handles Python / git / ffmpeg / Node / Ollama via winget, builds
the venv, pulls the model, and makes the Desktop shortcut. The only manual bit
is the YouTube cookies file (it prints how).
"@
