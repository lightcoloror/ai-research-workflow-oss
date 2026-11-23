[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$Candidate,

  [Parameter(Mandatory = $true)]
  [string]$Original,

  [ValidateSet("All", "PostTimeout")]
  [string]$Case = "All"
)

$ErrorActionPreference = "Stop"

$script:currentCase = "Setup"
$script:postCount = 0
$script:cliCount = 0
$script:metaCount = 0
$script:received = $false
$script:stubMode = "success"

function Invoke-RestMethod {
  [CmdletBinding()]
  param([Parameter(ValueFromRemainingArguments = $true)]$RestArgs)
  $script:postCount++
  if ($script:stubMode -eq "timeout") {
    $script:received = $false
    throw [System.TimeoutException]::new("ERR_TIMEOUT_SIMULATED")
  }
  if ($script:stubMode -eq "received-error") {
    $script:received = $true
    throw [System.IO.IOException]::new("ERR_RECEIVED_THEN_ERROR_SIMULATED")
  }
  return [pscustomobject]@{ status = "ok"; service = "ai-research-workflow" }
}

function Add-TransportMetadata {
  [CmdletBinding()]
  param([Parameter(ValueFromRemainingArguments = $true)]$MetaArgs)
  $script:metaCount++
  if ($script:stubMode -eq "decorate-error") {
    throw [System.InvalidOperationException]::new("ERR_DECORATE_FAILED_SIMULATED")
  }
  return [pscustomobject]@{ transport = "http"; reason = "ok" }
}

function Invoke-ResearchCli {
  [CmdletBinding()]
  param([Parameter(ValueFromRemainingArguments = $true)]$CliArgs)
  $script:cliCount++
  return "cli_output"
}

foreach ($stub in @("Invoke-RestMethod", "Add-TransportMetadata", "Invoke-ResearchCli")) {
  $cmd = Get-Command -Name $stub -CommandType Function -ErrorAction Stop
  if ($cmd.CommandType -ne [System.Management.Automation.CommandTypes]::Function) {
    Write-Host "ARW_GUARD_FAIL:Setup:Stub $stub is not a Function"
    exit 1
  }
}

try {
  $candText = Get-Content -LiteralPath $Candidate -Raw -Encoding UTF8
  $origText = Get-Content -LiteralPath $Original -Raw -Encoding UTF8

  $tokens = $null; $candErrors = $null
  $candAst = [System.Management.Automation.Language.Parser]::ParseInput($candText, [ref]$tokens, [ref]$candErrors)
  if ($candErrors.Count -gt 0) {
    throw "Candidate parse error: $($candErrors[0].Message)"
  }

  $tokens = $null; $origErrors = $null
  $origAst = [System.Management.Automation.Language.Parser]::ParseInput($origText, [ref]$tokens, [ref]$origErrors)
  if ($origErrors.Count -gt 0) {
    throw "Original parse error: $($origErrors[0].Message)"
  }

  $candLastTry = ($candAst.EndBlock.Statements | Where-Object { $_ -is [System.Management.Automation.Language.TryStatementAst] })[-1]
  if ($null -eq $candLastTry) {
    throw "Could not find top-level TryStatementAst in candidate"
  }

  $allowed = @("Invoke-RestMethod", "ConvertTo-Json", "Add-TransportMetadata", "Invoke-ResearchCli")
  $cmds = $candLastTry.FindAll({ $args[0] -is [System.Management.Automation.Language.CommandAst] }, $true)
  foreach ($c in $cmds) {
    $cmdName = $c.GetCommandName()
    if ($cmdName -and $cmdName -notin $allowed) {
      throw "Disallowed command in try block: $cmdName"
    }
  }

  $tryBlock = [scriptblock]::Create($candLastTry.Extent.Text)

  if ($Case -eq "All") {
    $script:currentCase = "Preservation"
    $origLastTry = ($origAst.EndBlock.Statements | Where-Object { $_ -is [System.Management.Automation.Language.TryStatementAst] })[-1]
    if ($null -eq $origLastTry) {
      throw "Could not find top-level TryStatementAst in original"
    }

    $candCatch = $candLastTry.CatchClauses[0]
    $origCatch = $origLastTry.CatchClauses[0]

    if ($candCatch.Extent.Text -match "Invoke-ResearchCli") {
      throw "Candidate catch must not contain Invoke-ResearchCli"
    }
    if ($candCatch.Extent.Text -notmatch "throw") {
      throw "Candidate catch must contain throw statement"
    }

    $oldReason = "configured HTTP service was not healthy; used same-project CLI without starting a process"
    $newReason = "CLI selected before POST: health unavailable or md-format request"

    $candWithoutCatch = $candText.Substring(0, $candCatch.Extent.StartOffset) + $candText.Substring($candCatch.Extent.EndOffset)
    $origWithoutCatch = $origText.Substring(0, $origCatch.Extent.StartOffset) + $origText.Substring($origCatch.Extent.EndOffset)

    $candNorm = ($candWithoutCatch.Replace($newReason, $oldReason) -replace '\r\n', "`n").Trim()
    $origNorm = ($origWithoutCatch -replace '\r\n', "`n").Trim()
    if ($candNorm -ne $origNorm) {
      throw "Whole-source comparison failed: unauthorized changes detected outside CLI reason and final catch"
    }

    $script:currentCase = "Preconditions"
    $candPrecondIf = $candAst.FindAll({ $args[0] -is [System.Management.Automation.Language.IfStatementAst] -and $args[0].Clauses[0].Item1.Extent.Text -match '\$healthy' }, $true) | Select-Object -First 1
    $origPrecondIf = $origAst.FindAll({ $args[0] -is [System.Management.Automation.Language.IfStatementAst] -and $args[0].Clauses[0].Item1.Extent.Text -match '\$healthy' }, $true) | Select-Object -First 1
    if ($null -eq $candPrecondIf -or $null -eq $origPrecondIf) {
      throw "Could not locate health/format precondition if statement"
    }

    if ($candPrecondIf.Clauses[0].Item2.Extent.Text.Trim() -ne $origPrecondIf.Clauses[0].Item2.Extent.Text.Trim()) {
      throw "Precondition body including exit 0 was modified from original"
    }

    $condBlock = [scriptblock]::Create($candPrecondIf.Clauses[0].Item1.Extent.Text)
    $healthy = $false; $Format = "json"
    if (-not (& $condBlock)) { throw "Precondition unhealthy/json expected true" }
    $healthy = $true; $Format = "md"
    if (-not (& $condBlock)) { throw "Precondition healthy/md expected true" }
    $healthy = $true; $Format = "json"
    if (& $condBlock) { throw "Precondition healthy/json expected false" }

    $script:currentCase = "Success"
    $script:postCount = 0; $script:cliCount = 0; $script:metaCount = 0; $script:received = $false; $script:stubMode = "success"
    $baseUrl = "http://127.0.0.1:8000"; $path = "/v1/search"; $payload = @{ topic = "synthetic-query"; mode = "auto" }
    $succErr = $null
    try { & $tryBlock } catch { $succErr = $_ }
    if ($null -ne $succErr) { throw "Success case unexpectedly threw: $($succErr.Exception.Message)"
    }
    if ($script:postCount -ne 1 -or $script:cliCount -ne 0 -or $script:metaCount -ne 1) {
      throw "Success case assertion failed (POST=$($script:postCount), CLI=$($script:cliCount), META=$($script:metaCount))"
    }
  }

  $script:currentCase = "PostTimeout"
  $script:postCount = 0; $script:cliCount = 0; $script:metaCount = 0; $script:received = $false; $script:stubMode = "timeout"
  $baseUrl = "http://127.0.0.1:8000"; $path = "/v1/search"; $payload = @{ topic = "synthetic-query"; mode = "auto" }
  $timeoutThrew = $false; $timeoutErr = $null
  try { & $tryBlock } catch { $timeoutThrew = $true; $timeoutErr = $_ }
  if ($script:postCount -ne 1) { throw "PostTimeout expected 1 POST attempt, got $($script:postCount)" }
  if ($script:cliCount -ne 0) { throw "PostTimeout expected 0 CLI calls (no fallback), got $($script:cliCount)" }
  if ($script:metaCount -ne 0) { throw "PostTimeout expected 0 metadata decorations, got $($script:metaCount)" }
  if ($script:received) { throw "PostTimeout expected received flag to be false" }
  if (-not $timeoutThrew) { throw "PostTimeout expected error propagation, but error was swallowed" }
  if ($timeoutErr.Exception.Message -notmatch "ERR_TIMEOUT_SIMULATED") {
    throw "PostTimeout unexpected error marker: $($timeoutErr.Exception.Message)"
  }

  if ($Case -eq "All") {
    $script:currentCase = "ReceivedError"
    $script:postCount = 0; $script:cliCount = 0; $script:metaCount = 0; $script:received = $false; $script:stubMode = "received-error"
    $baseUrl = "http://127.0.0.1:8000"; $path = "/v1/search"; $payload = @{ topic = "synthetic-query"; mode = "auto" }
    $recvThrew = $false; $recvErr = $null
    try { & $tryBlock } catch { $recvThrew = $true; $recvErr = $_ }
    if ($script:postCount -ne 1) { throw "ReceivedError expected 1 POST attempt, got $($script:postCount)" }
    if ($script:cliCount -ne 0) { throw "ReceivedError expected 0 CLI calls, got $($script:cliCount)" }
    if ($script:metaCount -ne 0) { throw "ReceivedError expected 0 metadata decorations, got $($script:metaCount)" }
    if (-not $script:received) { throw "ReceivedError expected received flag to be true" }
    if (-not $recvThrew) { throw "ReceivedError expected error propagation, but error was swallowed" }
    if ($recvErr.Exception.Message -notmatch "ERR_RECEIVED_THEN_ERROR_SIMULATED") {
      throw "ReceivedError unexpected error marker: $($recvErr.Exception.Message)"
    }

    $script:currentCase = "DecorateError"
    $script:postCount = 0; $script:cliCount = 0; $script:metaCount = 0; $script:received = $false; $script:stubMode = "decorate-error"
    $baseUrl = "http://127.0.0.1:8000"; $path = "/v1/search"; $payload = @{ topic = "synthetic-query"; mode = "auto" }
    $decThrew = $false; $decErr = $null
    try { & $tryBlock } catch { $decThrew = $true; $decErr = $_ }
    if ($script:postCount -ne 1) { throw "DecorateError expected 1 POST attempt, got $($script:postCount)" }
    if ($script:cliCount -ne 0) { throw "DecorateError expected 0 CLI calls, got $($script:cliCount)" }
    if ($script:metaCount -ne 1) { throw "DecorateError expected 1 metadata decoration attempt, got $($script:metaCount)" }
    if (-not $decThrew) { throw "DecorateError expected error propagation, but error was swallowed" }
    if ($decErr.Exception.Message -notmatch "ERR_DECORATE_FAILED_SIMULATED") {
      throw "DecorateError unexpected error marker: $($decErr.Exception.Message)"
    }
  }

  $summary = [pscustomobject]@{
    status = "passed"
    case = $Case
    candidate = $Candidate
  }
  Write-Host ($summary | ConvertTo-Json -Compress)
  exit 0
} catch {
  Write-Host "ARW_GUARD_FAIL:$($script:currentCase):$($_.Exception.Message)"
  exit 1
}
