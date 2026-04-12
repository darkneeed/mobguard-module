Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Get-EnvMap {
    param([string]$Path)

    $result = @{}
    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#') -or -not $trimmed.Contains('=')) {
            continue
        }
        $parts = $trimmed.Split('=', 2)
        $result[$parts[0].Trim()] = $parts[1].Trim()
    }
    return $result
}

if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
    Write-Host '[INFO] Created .env from .env.example'
}

$envMap = Get-EnvMap '.env'
$missing = @()
foreach ($key in @('PANEL_BASE_URL', 'MODULE_ID', 'MODULE_TOKEN', 'ACCESS_LOG_PATH')) {
    if (-not $envMap.ContainsKey($key)) {
        $missing += $key
    }
}
if ($missing.Count -gt 0) {
    throw "Missing required .env keys: $($missing -join ', ')"
}

New-Item -ItemType Directory -Force 'state', 'state\spool' | Out-Null

Get-Command docker | Out-Null
Get-Command python | Out-Null

docker compose build
@'
from mobguard_module.config import ModuleConfig
cfg = ModuleConfig.from_env('.env')
assert cfg.panel_base_url
assert cfg.module_id
assert cfg.module_token
assert cfg.access_log_path
print(cfg.module_id)
'@ | python -

Write-Host '[OK] Module build and smoke-check passed'
