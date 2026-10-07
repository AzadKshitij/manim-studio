<#
.SYNOPSIS
    Install git hooks (pre-commit, pre-push) from scripts/githooks.
.DESCRIPTION
    Copies the tracked hooks into .git/hooks. Re-run after pulling hook
    updates. The pre-commit hook runs the fast static gate (ruff format check,
    ruff, mypy on both platforms); the pre-push hook runs the CI test subset.
    Bypass either hook with --no-verify (not recommended).
#>
[CmdletBinding()]
param()
$ErrorActionPreference = "Stop"
$root = Join-Path $PSScriptRoot ".."
$gitDir = (git -C $root rev-parse --git-dir).Trim()
if (-not [System.IO.Path]::IsPathRooted($gitDir)) {
    $gitDir = Join-Path $root $gitDir
}
$hooksDir = Join-Path $gitDir "hooks"
foreach ($hook in @("pre-commit", "pre-push")) {
    Copy-Item (Join-Path $root "scripts/githooks/$hook") (Join-Path $hooksDir $hook) -Force
    Write-Host "installed: $hook"
}
if ($IsLinux -or $IsMacOS) {
    chmod +x (Join-Path $hooksDir "pre-commit") (Join-Path $hooksDir "pre-push")
}
