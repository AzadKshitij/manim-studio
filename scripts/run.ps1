<#
.SYNOPSIS
    Run the Manim Editor GUI from source.
.DESCRIPTION
    Thin wrapper around `uv run manim-editor gui ...`. Extra arguments are passed
    through, e.g.:  scripts\run.ps1 scenes\intro.py --size 1920x1080
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$GuiArgs
)
$ErrorActionPreference = "Stop"
Push-Location (Join-Path $PSScriptRoot "..")
try {
    uv run manim-editor gui @GuiArgs
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
