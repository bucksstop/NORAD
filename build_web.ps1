<#
build_web.ps1 - Build the NORAD web (pygbag/WebAssembly) version.

Stages only the files the game needs to run into .\websrc (with the entry
point renamed to main.py, as pygbag expects), then packages it with pygbag
using our custom page template (web_template.tmpl).

Usage:
    .\build_web.ps1            # build only; output in websrc\build\web
    .\build_web.ps1 -Serve     # build, then serve at http://localhost:8000

Requires: Python 3 with pygame-ce and pygbag installed
    py -m pip install pygame-ce pygbag
#>
param(
    [switch]$Serve,
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$dst  = Join-Path $root "websrc"

# Files the game needs at runtime (source -> destination-relative-path).
$files = @{
    "norad_game.py"        = "main.py"          # pygbag entry point
    "game_ai.py"           = "game_ai.py"
    "game_ai_expert.py"    = "game_ai_expert.py"
    "game_rules.py"        = "game_rules.py"
    "NORAD map.jpg"        = "NORAD map.jpg"
    "data\grid.json"       = "data\grid.json"
}

Write-Host "Staging web build into $dst ..."
if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
New-Item -ItemType Directory -Force (Join-Path $dst "data") | Out-Null
New-Item -ItemType Directory -Force (Join-Path $dst "assets\units") | Out-Null

foreach ($src in $files.Keys) {
    $target = Join-Path $dst $files[$src]
    Copy-Item (Join-Path $root $src) $target -Force
}
Copy-Item (Join-Path $root "assets\units\*") (Join-Path $dst "assets\units\") -Force

$total = [math]::Round(((Get-ChildItem $dst -Recurse -File | Measure-Object -Property Length -Sum).Sum)/1MB, 2)
Write-Host "Staged $total MB."

$template = Join-Path $root "web_template.tmpl"
$env:SDL_VIDEODRIVER = "dummy"   # keep pygbag's packaging step headless

if ($Serve) {
    Write-Host "Building and serving at http://localhost:$Port ..."
    py -m pygbag --template $template --port $Port (Join-Path $dst "main.py")
} else {
    Write-Host "Building (no server) ..."
    py -m pygbag --template $template --build (Join-Path $dst "main.py")
    Write-Host "Done. Output: $(Join-Path $dst 'build\web')"
}
