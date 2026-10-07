<#
.SYNOPSIS
    Run the performance benchmarks and record a JSON report.
.DESCRIPTION
    Wraps tests/test_perf.py with MANIM_STUDIO_PERF_REPORT set. Without -Micro it
    runs both the micro (codec/parser) and the end-to-end (real GL host) suites.
.EXAMPLE
    scripts\benchmark.ps1                     # records docs\perf\latest.json
    scripts\benchmark.ps1 -Output after.json  # records docs\perf\after.json
    scripts\benchmark.ps1 -Micro              # micro only, no GPU needed
#>
[CmdletBinding()]
param(
    [string]$Output = "latest.json",
    [switch]$Micro
)
$ErrorActionPreference = "Stop"
Push-Location (Join-Path $PSScriptRoot "..")
try {
    $env:MANIM_STUDIO_PERF_REPORT = Join-Path "docs\perf" $Output
    $selector = if ($Micro) { "slow" } else { "slow or gl" }
    uv run python -m pytest tests/test_perf.py -m $selector -s -q
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
