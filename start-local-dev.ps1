Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location $PSScriptRoot
& python -m mobguard_module.dev_local start @args
exit $LASTEXITCODE
