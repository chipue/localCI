[CmdletBinding()]
param(
    [string]$OutputDirectory = (Join-Path $PSScriptRoot "dist"),
    [string]$PackageName = "localci-windows.zip"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$requiredFiles = @(
    "localci",
    "packaging.ps1",
    "README.md",
    "WORKFLOW.md"
)
$requiredDirectories = @(
    "scripts",
    ".localci"
)
$optionalPaths = @(
    "docs",
    ".github"
)

foreach ($relativePath in $requiredFiles + $requiredDirectories) {
    $path = Join-Path $PSScriptRoot $relativePath
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Required package input is missing: $relativePath"
    }
}

$outputPath = [System.IO.Path]::GetFullPath((Join-Path $OutputDirectory $PackageName))
$outputRoot = Split-Path -Parent $outputPath
$stagingRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("localci-package-" + [guid]::NewGuid().ToString("N"))
$stagingContent = Join-Path $stagingRoot "localci"

try {
    New-Item -ItemType Directory -Path $stagingContent -Force | Out-Null
    New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null

    foreach ($relativePath in $requiredFiles + $requiredDirectories + $optionalPaths) {
        $source = Join-Path $PSScriptRoot $relativePath
        if (Test-Path -LiteralPath $source) {
            Copy-Item -LiteralPath $source -Destination (Join-Path $stagingContent $relativePath) -Recurse -Force
        }
    }

    if (Test-Path -LiteralPath $outputPath) {
        Remove-Item -LiteralPath $outputPath -Force
    }
    Compress-Archive -Path (Join-Path $stagingContent "*") -DestinationPath $outputPath -CompressionLevel Optimal

    $package = Get-Item -LiteralPath $outputPath
    if ($package.Length -le 0) {
        throw "Package was created but is empty: $outputPath"
    }
    Write-Output ("Created {0} ({1} bytes)" -f $package.FullName, $package.Length)
}
finally {
    if (Test-Path -LiteralPath $stagingRoot) {
        Remove-Item -LiteralPath $stagingRoot -Recurse -Force
    }
}
