param([Parameter(Mandatory=$true,Position=0)][ValidateSet("doctor","preflight","scan","explain","plan","verify-plan")][string]$Command,[Parameter(ValueFromRemainingArguments=$true)][string[]]$AdapterArgs)
$repoRoot=Split-Path -Parent $PSScriptRoot
$adapter=Join-Path (Split-Path -Parent $repoRoot) "artifact-lifecycle-core\research_evidence_adapter.py"
$config=Join-Path $repoRoot "artifact-lifecycle\research-evidence-lifecycle.v1.json"
if(-not(Test-Path -LiteralPath $adapter)-or -not(Test-Path -LiteralPath $config)){Write-Error "artifact lifecycle adapter unavailable";exit 2}
& python $adapter $Command --config $config @AdapterArgs
exit $LASTEXITCODE
