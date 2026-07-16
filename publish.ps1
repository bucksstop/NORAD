<#
publish.ps1 - Save your changes AND put the game online, in one command.

Does both steps for you:
  1. Commits any source-code changes  (git add -A + git commit)
  2. Rebuilds and deploys the browser build to GitHub Pages (deploy_web.ps1)

Safe to run even when nothing changed - it just skips the source commit and
re-publishes the current version.

Usage:
    .\publish.ps1 "what changed"     # e.g. .\publish.ps1 "make fighters faster"
    .\publish.ps1 make fighters faster   # quotes optional - words are joined
    .\publish.ps1                    # no message: uses a dated one

The live game (https://bucksstop.github.io/NORAD/) updates about a minute
after this finishes.
#>
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$MessageParts)

# git writes normal progress to stderr; don't let PowerShell treat that as fatal.
$ErrorActionPreference = "Continue"
$root = $PSScriptRoot
Set-Location $root

$Message = ($MessageParts -join ' ').Trim()
if ($Message -eq "") { $Message = "Update $(Get-Date -Format 'yyyy-MM-dd HH:mm')" }

# 1. Save source-code changes (skip cleanly if there are none).
git add -A
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    git commit -m $Message
    if ($LASTEXITCODE -ne 0) { Write-Host "git commit failed - stopping."; exit 1 }
    Write-Host "Saved source changes: $Message"
} else {
    Write-Host "No source changes to save - re-publishing current version."
}

# 2. Build the web version, refresh docs/, commit it, and push (Pages redeploys).
& (Join-Path $root "deploy_web.ps1") -Message $Message

# Safety net: make sure the source commit is pushed too, even when deploy_web
# found no web changes to push (a no-op "up to date" otherwise).
Set-Location $root
git push
