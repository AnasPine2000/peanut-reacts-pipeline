# Rhubarb Lip Sync installer for Windows.
#
# Downloads the v1.14.0 release into third_party/ so the lip-sync
# pipeline can find it without requiring a system-wide install.
# Idempotent — safe to re-run; skips download if already present.
#
# Usage:
#   pwsh scripts/setup_rhubarb.ps1

$ErrorActionPreference = "Stop"

$Version = "1.14.0"
$Url = "https://github.com/DanielSWolf/rhubarb-lip-sync/releases/download/v$Version/Rhubarb-Lip-Sync-$Version-Windows.zip"
$Root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$ThirdParty = Join-Path $Root "third_party"
$TargetDir = Join-Path $ThirdParty "Rhubarb-Lip-Sync-$Version-Windows"
$Exe = Join-Path $TargetDir "rhubarb.exe"
$ZipPath = Join-Path $ThirdParty "rhubarb.zip"

if (Test-Path $Exe) {
  Write-Host "Rhubarb already installed: $Exe"
  exit 0
}

New-Item -ItemType Directory -Force -Path $ThirdParty | Out-Null

Write-Host "Downloading $Url ..."
Invoke-WebRequest -Uri $Url -OutFile $ZipPath -UseBasicParsing

Write-Host "Unzipping to $ThirdParty ..."
Expand-Archive -Path $ZipPath -DestinationPath $ThirdParty -Force
Remove-Item $ZipPath

if (-not (Test-Path $Exe)) {
  Write-Error "Install failed — expected $Exe not found after unzip."
  exit 1
}

Write-Host "OK: $Exe"
& $Exe --version
