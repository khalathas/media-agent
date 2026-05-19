#Requires -Version 5.1
<#
.SYNOPSIS
    Installs ffmpeg/ffprobe into vendor/ffmpeg/ for media-agent.

.DESCRIPTION
    Checks if ffprobe is already on PATH first. If so, no action is needed —
    media-agent will detect it automatically. Otherwise downloads a static
    ffmpeg build from gyan.dev and extracts it to vendor/ffmpeg/ relative
    to the repo root.

    Does NOT modify media_agent_config.json. Runtime discovery handles
    vendor/ffmpeg/ automatically (PATH → config.ffprobe_path → vendor/ffmpeg/).
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot  = Split-Path $PSScriptRoot -Parent
$VendorDir = Join-Path $RepoRoot 'vendor\ffmpeg'

# 1. Check PATH first — don't create a duplicate install
$Existing = Get-Command ffprobe -ErrorAction SilentlyContinue
if ($Existing) {
    Write-Host "ffprobe already available at: $($Existing.Source)" -ForegroundColor Green
    Write-Host "No install needed — media-agent will detect it automatically."
    exit 0
}

# 2. Check if vendor install already exists
$VendorProbe = Join-Path $VendorDir 'bin\ffprobe.exe'
if (Test-Path $VendorProbe) {
    Write-Host "ffprobe already installed in vendor/ffmpeg/: $VendorProbe" -ForegroundColor Green
    Write-Host "No install needed."
    exit 0
}

Write-Host "ffprobe not found on PATH. Installing to vendor/ffmpeg/ ..."

# Static build from gyan.dev — essentials build (no extra codecs, smaller download)
$BuildUrl  = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
$ZipPath   = Join-Path $env:TEMP 'ffmpeg-release-essentials.zip'
$ExtractTo = Join-Path $env:TEMP 'ffmpeg-extract'

try {
    Write-Host "Downloading $BuildUrl ..."
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $BuildUrl -OutFile $ZipPath -UseBasicParsing

    Write-Host "Extracting ..."
    if (Test-Path $ExtractTo) { Remove-Item $ExtractTo -Recurse -Force }
    Expand-Archive -Path $ZipPath -DestinationPath $ExtractTo -Force

    # The zip contains a single versioned top-level folder (e.g. ffmpeg-7.1-essentials_build)
    $TopLevel = Get-ChildItem $ExtractTo -Directory | Select-Object -First 1
    if (-not $TopLevel) { throw "Unexpected zip structure — no top-level directory found." }

    Write-Host "Installing to $VendorDir ..."
    if (Test-Path $VendorDir) { Remove-Item $VendorDir -Recurse -Force }
    New-Item -ItemType Directory -Force $VendorDir | Out-Null
    Copy-Item -Path (Join-Path $TopLevel.FullName 'bin') -Destination $VendorDir -Recurse

    # Verify
    $ProbeExe = Join-Path $VendorDir 'bin\ffprobe.exe'
    if (-not (Test-Path $ProbeExe)) { throw "ffprobe.exe not found after extraction." }
    $Version = & $ProbeExe -version 2>&1 | Select-Object -First 1
    Write-Host "Installed: $Version" -ForegroundColor Green
    Write-Host "Location : $ProbeExe"
    Write-Host ""
    Write-Host "media-agent will detect vendor/ffmpeg/ automatically. No config changes needed."
}
finally {
    if (Test-Path $ZipPath)   { Remove-Item $ZipPath   -Force -ErrorAction SilentlyContinue }
    if (Test-Path $ExtractTo) { Remove-Item $ExtractTo -Recurse -Force -ErrorAction SilentlyContinue }
}
