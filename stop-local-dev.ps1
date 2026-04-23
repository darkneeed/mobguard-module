Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location $PSScriptRoot
& python -m mobguard_module.dev_local stop @args
exit $LASTEXITCODE
