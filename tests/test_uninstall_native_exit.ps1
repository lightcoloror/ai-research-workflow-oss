$ErrorActionPreference = "Stop"
$CandidatePath = if ($PSScriptRoot) { Join-Path $PSScriptRoot "..\scripts\uninstall-windows-service.ps1" } else { "scripts/uninstall-windows-service.ps1" }
$tokens = $null; $parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($CandidatePath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -ne 0) { throw "Candidate parse failed: $($parseErrors[0].Message)" }

$ifAst = $ast.EndBlock.Statements | Where-Object { $_ -is [System.Management.Automation.Language.IfStatementAst] } | Select-Object -First 1
$nssmStmts = ($ifAst.Clauses[0].Item2.Statements | ForEach-Object { $_.Extent.Text }) -join "`n"
$finalStmt = $ast.EndBlock.Statements[-1].Extent.Text
$harnessBlock = [scriptblock]::Create("$nssmStmts`n$finalStmt")

function Test-CommandExists([string]$Command) { return $Command -eq "Invoke-SyntheticNssm" }

$script:nativeArgs = [System.Collections.Generic.List[string]]::new()
$script:behavior = @{}

function Invoke-SyntheticNssm {
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$CmdArgs)
  $op = $CmdArgs[0]
  $script:nativeArgs.Add(($CmdArgs -join " "))
  if ($script:behavior[$op].throws) { throw "Simulated native error during $op" }
  $exitVal = if ($null -ne $script:behavior[$op].exit) { [int]$script:behavior[$op].exit } else { 0 }
  Set-Variable -Name LASTEXITCODE -Value $exitVal -Scope 1
}

$cmd = Get-Command "Invoke-SyntheticNssm" -ErrorAction Stop
if ($cmd.CommandType -ne [System.Management.Automation.CommandTypes]::Function) { throw "Expected Function" }

function Invoke-TestCase([string]$CaseName, [hashtable]$Beh, [bool]$Legacy = $false) {
  $script:nativeArgs.Clear()
  $script:behavior = $Beh
  $Mode = "Nssm"; $ServiceName = "synthetic-arw-service"; $NssmPath = "Invoke-SyntheticNssm"
  Set-Variable -Name LASTEXITCODE -Value 0
  $caught = $null; $raw = $null; $parsed = $null
  try {
    if ($Legacy) {
      & $NssmPath stop $ServiceName
      & $NssmPath remove $ServiceName confirm
      $raw = [pscustomobject]@{ service_name = $ServiceName; mode = $Mode; removed = $true } | ConvertTo-Json -Depth 3
    } else {
      $raw = & $harnessBlock
    }
    if ($raw) { $parsed = $raw | ConvertFrom-Json }
  } catch {
    $caught = $_.Exception.Message
  }
  return [pscustomobject]@{
    case = $CaseName; calls = @($script:nativeArgs); threw = ($null -ne $caught)
    error = $caught; json = $parsed; is_legacy = $Legacy
  }
}

$results = [System.Collections.Generic.List[object]]::new()

# 1. all0 => stop/remove once + removed true
$r1 = Invoke-TestCase "all0" @{ stop = @{ exit = 0 }; remove = @{ exit = 0 } }
if ($r1.calls.Count -ne 2 -or $r1.calls[0] -ne "stop synthetic-arw-service" -or $r1.calls[1] -ne "remove synthetic-arw-service confirm" -or -not $r1.json.removed -or $r1.threw) { exit 1 }
$results.Add($r1)

# 2. stop7 => only stop + throw contains 7 + no JSON
$r2 = Invoke-TestCase "stop7" @{ stop = @{ exit = 7 }; remove = @{ exit = 0 } }
if ($r2.calls.Count -ne 1 -or $r2.calls[0] -ne "stop synthetic-arw-service" -or -not $r2.threw -or $r2.error -notmatch "7" -or $null -ne $r2.json) { exit 1 }
$results.Add($r2)

# 3. remove7 => 2 calls + throw contains 7 + no JSON
$r3 = Invoke-TestCase "remove7" @{ stop = @{ exit = 0 }; remove = @{ exit = 7 } }
if ($r3.calls.Count -ne 2 -or $r3.calls[0] -ne "stop synthetic-arw-service" -or $r3.calls[1] -ne "remove synthetic-arw-service confirm" -or -not $r3.threw -or $r3.error -notmatch "7" -or $null -ne $r3.json) {
  exit 1
}
$results.Add($r3)

# 4. stopthrows => only stop + no remove + no JSON
$r4 = Invoke-TestCase "stopthrows" @{ stop = @{ throws = $true }; remove = @{ exit = 0 } }
if ($r4.calls.Count -ne 1 -or $r4.calls[0] -ne "stop synthetic-arw-service" -or -not $r4.threw -or $null -ne $r4.json) { exit 1 }
$results.Add($r4)

# 5. removethrows => 2 calls + no JSON
$r5 = Invoke-TestCase "removethrows" @{ stop = @{ exit = 0 }; remove = @{ throws = $true } }
if ($r5.calls.Count -ne 2 -or $r5.calls[0] -ne "stop synthetic-arw-service" -or $r5.calls[1] -ne "remove synthetic-arw-service confirm" -or -not $r5.threw -or $null -ne $r5.json) { exit 1 }
$results.Add($r5)

# 6. legacycounterexample => explicitly labelled inline prior two unguarded calls + same JSON against exits 7, reproduce removed true bug
$r6 = Invoke-TestCase "legacycounterexample" @{ stop = @{ exit = 7 }; remove = @{ exit = 7 } } $true
if ($r6.calls.Count -ne 2 -or $r6.calls[0] -ne "stop synthetic-arw-service" -or $r6.calls[1] -ne "remove synthetic-arw-service confirm" -or $r6.threw -or -not $r6.json.removed) { exit 1 }
$results.Add($r6)

[pscustomobject]@{ total_cases = $results.Count; all_passed = $true; results = $results } | ConvertTo-Json -Depth 4
