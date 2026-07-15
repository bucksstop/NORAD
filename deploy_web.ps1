<#
deploy_web.ps1 - Publish the web game to GitHub Pages in one step.

Rebuilds the pygbag web package, refreshes docs/ from the fresh output, then
commits docs/ and pushes. GitHub Pages serves docs/ on the main branch, so the
push redeploys https://bucksstop.github.io/NORAD/ within about a minute.

Usage:
    .\deploy_web.ps1                        # timestamped deploy commit
    .\deploy_web.ps1 -Message "faster AI"   # custom commit message

Note: this commits ONLY docs/ (the built site). Commit your source-code
changes (norad_game.py, game_*.py, ...) separately with your normal git
workflow - deploying does not commit them for you.
#>
param([string]$Message = "")

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

# 1. Build the web package (regenerates websrc\build\web).
& (Join-Path $root "build_web.ps1")

# 2. Refresh docs/ (what GitHub Pages serves) from the fresh build output.
$docs = Join-Path $root "docs"
if (Test-Path $docs) { Remove-Item $docs -Recurse -Force }
New-Item -ItemType Directory -Force $docs | Out-Null
Copy-Item (Join-Path $root "websrc\build\web\*") $docs -Recurse -Force
New-Item -ItemType File -Force (Join-Path $docs ".nojekyll") | Out-Null

# 3. Commit docs/ and push; Pages redeploys automatically. git writes normal
#    progress to stderr, which PowerShell's Stop mode would treat as a fatal
#    error, so relax error handling here and check exit codes explicitly.
$ErrorActionPreference = "Continue"
git add docs
git diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
    Write-Host "No changes in docs/ - nothing to deploy."
    exit 0
}
if ($Message -eq "") { $Message = "Deploy web build $(Get-Date -Format 'yyyy-MM-dd HH:mm')" }
git commit -m $Message
if ($LASTEXITCODE -ne 0) { Write-Host "git commit failed (exit $LASTEXITCODE)."; exit 1 }
git push
if ($LASTEXITCODE -ne 0) { Write-Host "git push failed (exit $LASTEXITCODE)."; exit 1 }

Write-Host ""
Write-Host "Deployed. https://bucksstop.github.io/NORAD/ updates within ~a minute."
Write-Host "(Tip: hard-refresh the live page - Ctrl+Shift+R - to skip the browser cache.)"
