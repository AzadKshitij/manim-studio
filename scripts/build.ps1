<#
.SYNOPSIS
    Full release pipeline: quality gate, PyInstaller bundle, smoke tests,
    portable zip and (optionally) the Inno Setup installer.
.DESCRIPTION
    1. Quality gate (ruff format check, ruff check, mypy, pytest) unless -SkipGate.
    2. Regenerates the icon and builds dist\ManimEditor\ with PyInstaller
       (windowed ManimEditor.exe + console manim-editor.exe).
    3. Smoke-tests the frozen executables (CLI surface, frozen api-index,
       manimgl dispatch through pkg_resources, windowed stdio + host dispatch).
    4. Zips dist\ManimEditor-<version>-portable.zip.
    5. Builds the Inno Setup installer unless -SkipInstaller or ISCC is missing.

    Needs ffmpeg on PATH only for the export path at runtime, not for the build.
.EXAMPLE
    scripts\build.ps1
    scripts\build.ps1 -SkipTests        # gate without the pytest suite
    scripts\build.ps1 -SkipGate -SkipInstaller
#>
[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$SkipGate,
    [switch]$SkipInstaller
)
$ErrorActionPreference = "Stop"
Push-Location (Join-Path $PSScriptRoot "..")
try {
    # -- 1. Quality gate (same checks AGENTS.md requires before every commit) --
    if (-not $SkipGate) {
        Write-Host "==> quality gate"
        uv run ruff format --check src tests
        if ($LASTEXITCODE -ne 0) { throw "ruff format check failed" }
        uv run ruff check src tests
        if ($LASTEXITCODE -ne 0) { throw "ruff check failed" }
        uv run mypy
        if ($LASTEXITCODE -ne 0) { throw "mypy failed" }
        if (-not $SkipTests) {
            uv run pytest
            if ($LASTEXITCODE -ne 0) { throw "pytest failed" }
        }
    }

    $version = (uv run python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
    if ($LASTEXITCODE -ne 0) { throw "cannot read version from pyproject.toml" }
    $version = $version.Trim()
    Write-Host "==> building Manim Editor $version"

    # -- 2. Icon (deterministic; regenerating is cheap and keeps it in sync) --
    uv run python packaging\make_icon.py
    if ($LASTEXITCODE -ne 0) { throw "icon generation failed" }

    # -- 3. PyInstaller bundle --
    # Explicit dist/work paths: the spec lives in packaging/, but all outputs
    # belong at the repo root (dist/, build/) where clean.ps1 expects them.
    uv run pyinstaller --noconfirm --clean --distpath dist --workpath build packaging\manim_editor.spec
    if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed" }

    $bundleDir = Join-Path (Get-Location) "dist\ManimEditor"
    $guiExe = Join-Path $bundleDir "ManimEditor.exe"
    $cliExe = Join-Path $bundleDir "manim-editor.exe"
    foreach ($exe in ($guiExe, $cliExe)) {
        if (-not (Test-Path $exe)) { throw "expected executable missing: $exe" }
    }

    # -- 4. Smoke tests on the frozen executables --
    function Invoke-Smoke {
        param(
            [Parameter(Mandatory)] [string]$Label,
            [Parameter(Mandatory)] [string]$Exe,
            [Parameter(Mandatory)] [string[]]$Arguments,
            [string]$ExpectOutput = ""
        )
        Write-Host "  smoke: $Label"
        $output = & $Exe @Arguments 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0) { throw "smoke '$Label' exited ${LASTEXITCODE}: $output" }
        if ($ExpectOutput -ne "" -and $output -notmatch $ExpectOutput) {
            throw "smoke '$Label' output mismatch: $output"
        }
    }

    Write-Host "==> smoke tests"
    $escapedVersion = [regex]::Escape("manim-editor $version")
    Invoke-Smoke -Label "cli --version" -Exe $cliExe -Arguments @("--version") -ExpectOutput $escapedVersion
    Invoke-Smoke -Label "cli check" -Exe $cliExe `
        -Arguments @("check", "tests\scenes\feature_showcase.py") -ExpectOutput "OK"
    Invoke-Smoke -Label "cli templates" -Exe $cliExe -Arguments @("templates")
    # Exercises the frozen api-index fallback (bundled manimlib sources).
    Invoke-Smoke -Label "cli api-index" -Exe $cliExe -Arguments @("api-index") -ExpectOutput "Circle"
    # Exercises manimgl import through pkg_resources inside the frozen process.
    Invoke-Smoke -Label "cli manimgl dispatch" -Exe $cliExe `
        -Arguments @("--manimgl-export", "--help") -ExpectOutput "write_file"
    # The windowed exe must still serve stdio when the parent redirects it …
    Invoke-Smoke -Label "gui --version" -Exe $guiExe -Arguments @("--version") -ExpectOutput $escapedVersion
    # … and route the internal host flag instead of opening the editor.
    Invoke-Smoke -Label "gui host dispatch" -Exe $guiExe `
        -Arguments @("--preview-host", "--help") -ExpectOutput "port"

    # -- 5. Portable zip (folder name inside the archive is ManimEditor/) --
    $zipPath = Join-Path (Get-Location) "dist\ManimEditor-$version-portable.zip"
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
    Compress-Archive -Path $bundleDir -DestinationPath $zipPath
    Write-Host "==> portable: $zipPath"

    # -- 6. Inno Setup installer --
    if (-not $SkipInstaller) {
        $iscc = $null
        $candidates = @(
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
            "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
            "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
        )
        foreach ($candidate in $candidates) {
            if (Test-Path $candidate) { $iscc = $candidate; break }
        }
        if ($null -eq $iscc) {
            $isccCommand = Get-Command iscc -ErrorAction SilentlyContinue
            if ($null -ne $isccCommand) {
                $iscc = $isccCommand.Source
            }
        }
        if ($null -eq $iscc) {
            Write-Warning "Inno Setup (ISCC.exe) not found; skipping the installer (portable zip is ready)."
        }
        else {
            Write-Host "==> installer via $iscc"
            & $iscc /DAppVersion=$version packaging\installer.iss
            if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
        }
    }

    # -- 7. Summary --
    Write-Host "==> artifacts"
    Get-ChildItem dist\ManimEditor-*.zip, dist\ManimEditor-Setup-*.exe -ErrorAction SilentlyContinue |
        ForEach-Object { Write-Host ("  {0}  ({1:N1} MB)" -f $_.Name, ($_.Length / 1MB)) }
    $bundleSize = (Get-ChildItem $bundleDir -Recurse -File | Measure-Object Length -Sum).Sum
    Write-Host ("  {0}\  ({1:N1} MB unpacked)" -f $bundleDir, ($bundleSize / 1MB))
}
finally {
    Pop-Location
}
