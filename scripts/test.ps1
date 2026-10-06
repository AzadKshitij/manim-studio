<#
.SYNOPSIS
    Run the pytest suite (Qt offscreen, GL tests auto-skip when unavailable).
.EXAMPLE
    scripts\test.ps1 -m "not gl and not slow"
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs
)
$ErrorActionPreference = "Stop"
Push-Location (Join-Path $PSScriptRoot "..")
try {
    uv run pytest @PytestArgs
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
