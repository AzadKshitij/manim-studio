<#
.SYNOPSIS
    Full pre-commit gate: format check + ruff + mypy + pytest.
.DESCRIPTION
    Same checks AGENTS.md requires before every commit. Pass -Fast to skip the
    slow GL/ffmpeg tests (`pytest -m "not gl and not slow"`).
#>
[CmdletBinding()]
param(
    [switch]$Fast
)
$ErrorActionPreference = "Stop"
Push-Location (Join-Path $PSScriptRoot "..")
try {
    & (Join-Path $PSScriptRoot "lint.ps1")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    if ($Fast) {
        uv run pytest -m "not gl and not slow"
    }
    else {
        uv run pytest
    }
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
