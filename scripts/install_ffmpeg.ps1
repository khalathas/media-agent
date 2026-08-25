#Requires -Version 5.1
<#
.SYNOPSIS
    Installs ffmpeg/ffprobe into vendor/ffmpeg/ for media-agent.

.DESCRIPTION
    PREFER YOUR PACKAGE MANAGER INSTEAD:  winget install Gyan.FFmpeg
    That verifies the package signature for you. This script does not have
    access to a signature, but it does now verify a pinned SHA-256 checksum
    before extracting anything, and it inspects the zip's entries for
    path-traversal attempts before extracting.

    Checks if ffprobe is already on PATH first. If so, no action is needed —
    media-agent will detect it automatically. Otherwise it downloads a
    pinned, checksum-verified static ffmpeg build from gyan.dev (version
    fixed below — not a "latest" alias that can change underneath us) and
    extracts it to vendor/ffmpeg/ relative to the repo root.

    Does NOT modify media_agent_config.json. Runtime discovery handles
    vendor/ffmpeg/ automatically (PATH -> config.ffprobe_path -> vendor/ffmpeg/).

.PARAMETER NoRun
    Load the functions defined in this script without executing the
    installer. Used by the test suite to dot-source and unit test the
    checksum/extraction helpers without performing a real install.
#>
param(
    [switch]$NoRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot  = Split-Path $PSScriptRoot -Parent
$VendorDir = Join-Path $RepoRoot 'vendor\ffmpeg'

# Pinned release. Bump these together (version, URL, hash) after manually
# downloading the new build and computing its SHA-256 — do not paste in a
# hash you haven't verified yourself against a real download.
$FfmpegVersion       = '9.0.1'
$FfmpegBuildUrl       = "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-$FfmpegVersion-essentials_build.zip"
$FfmpegExpectedSha256 = 'fec81ae03971d9dd4be3ebe02e263bd2ec1d789483f931bdba5f5715e65da2e9'

function Test-Sha256Checksum {
    <#
    Verifies that the file at $Path has SHA-256 hash $Expected.
    Returns $true/$false; never throws for a mismatch (only for I/O errors).
    #>
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Expected
    )
    $actual = (Get-FileHash -Path $Path -Algorithm SHA256).Hash
    return ($actual.ToLowerInvariant() -eq $Expected.ToLowerInvariant())
}

function Test-SafeZipEntries {
    <#
    Inspects every entry in the zip at $Path and rejects the archive if any
    entry would extract outside the destination directory: absolute paths,
    drive-letter paths, or any ".." path segment.
    #>
    param(
        [Parameter(Mandatory)][string]$Path
    )
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($Path)
    try {
        foreach ($entry in $zip.Entries) {
            $name = $entry.FullName -replace '\\', '/'
            if ($name -match '^(/|[A-Za-z]:)') {
                Write-Warning "Unsafe zip entry (absolute path): $name"
                return $false
            }
            $segments = $name -split '/'
            if ($segments -contains '..') {
                Write-Warning "Unsafe zip entry (path traversal): $name"
                return $false
            }
        }
    }
    finally {
        $zip.Dispose()
    }
    return $true
}

function Expand-ZipSafely {
    <#
    Extracts the zip at $Path into $Destination entry-by-entry, re-checking
    that each resolved target path stays inside $Destination. $Destination
    must already exist. Call Test-SafeZipEntries first for a clear error
    message; this is a second, independent check (defense in depth).
    #>
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Destination
    )
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $destFull = [System.IO.Path]::GetFullPath($Destination)
    if (-not $destFull.EndsWith([System.IO.Path]::DirectorySeparatorChar)) {
        $destFull += [System.IO.Path]::DirectorySeparatorChar
    }

    $zip = [System.IO.Compression.ZipFile]::OpenRead($Path)
    try {
        foreach ($entry in $zip.Entries) {
            $name = $entry.FullName -replace '\\', '/'
            $targetPath = Join-Path $Destination $name
            $targetFull = [System.IO.Path]::GetFullPath($targetPath)
            if (-not $targetFull.StartsWith($destFull, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Refusing to extract unsafe zip entry outside destination: $name"
            }
            if ($name.EndsWith('/') -or $entry.Name -eq '') {
                New-Item -ItemType Directory -Force -Path $targetFull | Out-Null
            }
            else {
                $targetDir = Split-Path $targetFull -Parent
                if (-not (Test-Path $targetDir)) {
                    New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
                }
                [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $targetFull, $true)
            }
        }
    }
    finally {
        $zip.Dispose()
    }
}

function Install-Ffmpeg {
    # 1. Check PATH first — don't create a duplicate install
    $Existing = Get-Command ffprobe -ErrorAction SilentlyContinue
    if ($Existing) {
        Write-Host "ffprobe already available at: $($Existing.Source)" -ForegroundColor Green
        Write-Host "No install needed — media-agent will detect it automatically."
        return
    }

    # 2. Check if vendor install already exists
    $VendorProbe = Join-Path $VendorDir 'bin\ffprobe.exe'
    if (Test-Path $VendorProbe) {
        Write-Host "ffprobe already installed in vendor/ffmpeg/: $VendorProbe" -ForegroundColor Green
        Write-Host "No install needed."
        return
    }

    Write-Host "ffprobe not found on PATH. Installing to vendor/ffmpeg/ ..."

    $ZipPath   = Join-Path $env:TEMP 'ffmpeg-release-essentials.zip'
    $ExtractTo = Join-Path $env:TEMP 'ffmpeg-extract'

    try {
        Write-Host "Downloading $FfmpegBuildUrl ..."
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $FfmpegBuildUrl -OutFile $ZipPath -UseBasicParsing

        Write-Host "Verifying checksum ..."
        if (-not (Test-Sha256Checksum -Path $ZipPath -Expected $FfmpegExpectedSha256)) {
            $actual = (Get-FileHash -Path $ZipPath -Algorithm SHA256).Hash
            throw "Checksum mismatch for downloaded archive.`n  expected: $FfmpegExpectedSha256`n  actual  : $actual`nThe download will NOT be extracted or executed. This can mean the download was corrupted, or that the upstream file changed."
        }
        Write-Host "Checksum OK." -ForegroundColor Green

        Write-Host "Checking archive contents for unsafe paths ..."
        if (-not (Test-SafeZipEntries -Path $ZipPath)) {
            throw "Refusing to extract an archive with unsafe member paths."
        }

        Write-Host "Extracting ..."
        if (Test-Path $ExtractTo) { Remove-Item $ExtractTo -Recurse -Force }
        New-Item -ItemType Directory -Force -Path $ExtractTo | Out-Null
        Expand-ZipSafely -Path $ZipPath -Destination $ExtractTo

        # The zip contains a single versioned top-level folder (e.g. ffmpeg-9.0.1-essentials_build)
        $TopLevel = Get-ChildItem $ExtractTo -Directory | Select-Object -First 1
        if (-not $TopLevel) { throw "Unexpected zip structure — no top-level directory found." }

        Write-Host "Installing to $VendorDir ..."
        if (Test-Path $VendorDir) { Remove-Item $VendorDir -Recurse -Force }
        New-Item -ItemType Directory -Force $VendorDir | Out-Null
        Copy-Item -Path (Join-Path $TopLevel.FullName 'bin') -Destination $VendorDir -Recurse

        # Verify — ffprobe writes version to stderr; use local Continue scope to avoid
        # $ErrorActionPreference = 'Stop' aborting on the stderr output (PS 5.1 quirk)
        $ProbeExe = Join-Path $VendorDir 'bin\ffprobe.exe'
        if (-not (Test-Path $ProbeExe)) { throw "ffprobe.exe not found after extraction." }
        $Version = & { $ErrorActionPreference = 'Continue'; & $ProbeExe -version 2>&1 | Select-Object -First 1 }
        Write-Host "Installed: $Version" -ForegroundColor Green
        Write-Host "Location : $ProbeExe"
        Write-Host ""
        Write-Host "media-agent will detect vendor/ffmpeg/ automatically. No config changes needed."
    }
    finally {
        if (Test-Path $ZipPath)   { Remove-Item $ZipPath   -Force -ErrorAction SilentlyContinue }
        if (Test-Path $ExtractTo) { Remove-Item $ExtractTo -Recurse -Force -ErrorAction SilentlyContinue }
    }
}

if (-not $NoRun) {
    Install-Ffmpeg
}
