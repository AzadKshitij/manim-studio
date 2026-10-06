<#
.SYNOPSIS
    Lint gate: ruff format check + ruff check + mypy.
.DESCRIPTION
    Default runs the read-only checks (same as the pre-commit gate).
    -Fix applies ruff format and safe autofixes instead.
.EXAMPLE
    scripts\lint.ps1
    scripts\lint.ps1 -Fix
#>
[CmdletBinding()]
param(
    [switch]$Fix
)
$ErrorActionPreference = "Stop"
Push-Location (Join-Path $PSScriptRoot "..")
try {
    if ($Fix) {
        uv run ruff format src tests
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        uv run ruff check --fix src tests
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    else {
        uv run ruff format --check src tests
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        uv run ruff check src tests
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    uv run mypy
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
