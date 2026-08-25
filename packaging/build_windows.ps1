param(
    [switch]$SkipInstall
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$distDir = Join-Path $projectRoot 'dist\VCGDownloader'
$releaseDir = Join-Path $projectRoot 'release'
$architecture = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'x64' }
$archivePath = Join-Path $releaseDir "VCGDownloader-Windows-$architecture.zip"

Push-Location $projectRoot
try {
    if (-not $SkipInstall) {
        python -m pip install -r requirements.txt -r requirements-build.txt
    }

    python -m PyInstaller --noconfirm --clean packaging\vcg.spec
    if ($LASTEXITCODE -ne 0) {
        throw 'PyInstaller build failed.'
    }

    New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null
    if (Test-Path -LiteralPath $archivePath) {
        Remove-Item -LiteralPath $archivePath -Force
    }
    Compress-Archive -Path $distDir -DestinationPath $archivePath -CompressionLevel Optimal

    Write-Host "Windows package created: $archivePath"
} finally {
    Pop-Location
}
