<#
.SYNOPSIS
    Remove build outputs and tooling caches.
.DESCRIPTION
    Default: dist/, build/, PyInstaller/icon artifacts, __pycache__, and the
    pytest/ruff/mypy/coverage caches. -Venv additionally removes .venv (the next
    `uv run` or `scripts\run.ps1` recreates it).
#>
[CmdletBinding()]
param(
    [switch]$Venv
)
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")

$targets = @(
    "dist",
    "build",
    "packaging\manim_editor.ico",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".coverage",
    "coverage.xml",
    "htmlcov"
)
if ($Venv) { $targets += ".venv" }

$removed = 0
foreach ($target in $targets) {
    $path = Join-Path $root $target
    if (Test-Path $path) {
        Remove-Item -Path $path -Recurse -Force
        Write-Host "removed $target"
        $removed++
    }
}

# Byte-code caches under the source trees (never inside .venv unless -Venv).
$cacheDirs = Get-ChildItem -Path (Join-Path $root "src"), (Join-Path $root "tests"), `
    (Join-Path $root "packaging"), (Join-Path $root "scenes") `
    -Directory -Recurse -Filter "__pycache__" -ErrorAction SilentlyContinue
foreach ($dir in $cacheDirs) {
    Remove-Item -Path $dir.FullName -Recurse -Force
    Write-Host "removed $($dir.FullName.Substring($root.Path.Length + 1))"
    $removed++
}

Write-Host "clean: $removed target(s) removed"
