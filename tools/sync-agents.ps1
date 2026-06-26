# sync-agents.ps1 — mirror senpai-v2 agents + skill from the repo into ~/.claude
# Repo is the source of truth; the local (~/.claude) copy is a convenience mirror so
# the agents/skill are available outside this project too. Run on demand after edits.
#
#   pwsh tools/sync-agents.ps1            # repo -> ~/.claude (default)
#   pwsh tools/sync-agents.ps1 -Pull      # ~/.claude -> repo (recover local edits)

param([switch]$Pull)

$ErrorActionPreference = 'Stop'
$repo  = Split-Path -Parent $PSScriptRoot
$home_ = $env:USERPROFILE

$pairs = @(
    @{ Repo = Join-Path $repo  '.claude\agents';          Home = Join-Path $home_ '.claude\agents';          Filter = 'senpai-v2-*.md' },
    @{ Repo = Join-Path $repo  '.claude\skills\senpai-v2'; Home = Join-Path $home_ '.claude\skills\senpai-v2'; Filter = '*' }
)

foreach ($p in $pairs) {
    if ($Pull) { $src = $p.Home; $dst = $p.Repo } else { $src = $p.Repo; $dst = $p.Home }
    if (-not (Test-Path $src)) { Write-Warning "skip (missing): $src"; continue }
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    Copy-Item -Path (Join-Path $src $p.Filter) -Destination $dst -Recurse -Force
    Write-Host "synced: $src -> $dst"
}
Write-Host "done ($(if ($Pull) {'pull'} else {'push'}))."
