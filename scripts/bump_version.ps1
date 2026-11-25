<#
.SYNOPSIS
    Unified Version Bumper for League Voice Recorder (PowerShell)
.EXAMPLE
    .\scripts\bump_version.ps1 0.6.1
    .\scripts\bump_version.ps1 -Patch
    .\scripts\bump_version.ps1 -Minor
    .\scripts\bump_version.ps1 -Major
#>

param(
    [Parameter(Position = 0)]
    [string]$Version,
    [switch]$Patch,
    [switch]$Minor,
    [switch]$Major
)

$ErrorActionPreference = "Stop"
$rootDir = Split-Path -Parent $PSScriptRoot

$cargoFile = Join-Path $rootDir "Cargo.toml"
$nsiFile   = Join-Path $rootDir "installer.nsi"

# 1. Read current version from Cargo.toml
$cargoContent = Get-Content $cargoFile -Raw -Encoding utf8
if ($cargoContent -match '(?m)^version\s*=\s*"([^"]+)"') {
    $currentVer = $matches[1]
} else {
    throw "Could not determine current version from Cargo.toml"
}

# 2. Compute new version
$parts = $currentVer.Split('.')
[int]$maj = $parts[0]
[int]$min = $parts[1]
[int]$pat = $parts[2]

if ($Major) {
    $newVer = "$($maj + 1).0.0"
} elseif ($Minor) {
    $newVer = "$maj.$($min + 1).0"
} elseif ($Patch) {
    $newVer = "$maj.$min.$($pat + 1)"
} elseif ($Version) {
    $newVer = $Version.TrimStart('v','V').Trim()
} else {
    Write-Host "Current version: $currentVer"
    Write-Host "Usage: .\scripts\bump_version.ps1 <X.Y.Z | -Patch | -Minor | -Major>"
    exit 1
}

Write-Host "🚀 Bumping League Voice Recorder version: $currentVer -> $newVer`n"

# 3. Update Cargo.toml
$updatedCargo = [regex]::Replace($cargoContent, '(?m)^(version\s*=\s*)"[^"]+"', "`${1}`"$newVer`"")
[System.IO.File]::WriteAllText($cargoFile, $updatedCargo, [System.Text.Encoding]::UTF8)
Write-Host "  ✓ Cargo.toml -> $newVer"

# 4. Update installer.nsi (with UTF-8 BOM)
$nsiContent = Get-Content $nsiFile -Raw -Encoding utf8
$updatedNsi = [regex]::Replace($nsiContent, '(!define\s+APP_VERSION\s+)"[^"]+"', "`${1}`"$newVer`"")
$utf8Bom = New-Object System.Text.UTF8Encoding $true
[System.IO.File]::WriteAllText($nsiFile, $updatedNsi, $utf8Bom)
Write-Host "  ✓ installer.nsi -> $newVer"

Write-Host "`n✅ All unified version definitions updated to v$newVer!"
Write-Host "`nNext steps to commit and tag:"
Write-Host "  git commit -am `"chore(release): bump version to $newVer`""
Write-Host "  git tag v$newVer"
