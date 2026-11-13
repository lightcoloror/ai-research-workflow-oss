# Focused synthetic controls for the ARW start-ui PID no-clobber candidate.
#
# Usage (run from the checkout root; no native child, HTTP, service, browser or
# process termination; real .NET CreateNew/File.Move only on synthetic files):
#   powershell -NoProfile -File tests/check-ui-launcher-pid-controls.ps1 `
#     -CandidatePath scripts/start-ui.ps1 `
#     -ScratchDirectory .tmp\check-pid-controls
#
# Any failed control terminates the script and exits nonzero. The final report
# prints the candidate SHA256 so the caller can compare it to the reviewed
# source hash; the checker itself does not embed a fixed hash.

param(
  [Parameter(Mandatory)][string]$CandidatePath,
  [Parameter(Mandatory)][string]$ScratchDirectory
)

$ErrorActionPreference = 'Stop'

trap {
  [Console]::Error.WriteLine("check-pid-controls failed: $($_.Exception.Message)")
  exit 1
}

class FakeChild {
  [int]$Id
  [bool]$HasExited
  [int]$KillCount
  [int]$WaitCount
  [int]$LastWaitTimeout
  [bool]$WaitResult
  [int]$RefreshCount
  FakeChild([int]$id) {
    $this.Id = $id
    $this.HasExited = $false
    $this.KillCount = 0
    $this.WaitCount = 0
    $this.LastWaitTimeout = 0
    $this.WaitResult = $true
    $this.RefreshCount = 0
  }
  [void] Kill() {
    $this.KillCount = $this.KillCount + 1
    $this.HasExited = $true
  }
  [bool] WaitForExit([int]$milliseconds) {
    $this.WaitCount = $this.WaitCount + 1
    $this.LastWaitTimeout = $milliseconds
    return $this.WaitResult
  }
  [void] Refresh() {
    $this.RefreshCount = $this.RefreshCount + 1
  }
}

$taskRoot = Split-Path -Parent $PSScriptRoot
$originalPythonPath = $env:PYTHONPATH

if (-not [System.IO.Path]::IsPathRooted($CandidatePath)) {
  $CandidatePath = Join-Path $taskRoot $CandidatePath
}
$CandidatePath = [System.IO.Path]::GetFullPath($CandidatePath)
if (-not (Test-Path -LiteralPath $CandidatePath -PathType Leaf)) {
  throw "Candidate not found: $CandidatePath"
}

if (-not [System.IO.Path]::IsPathRooted($ScratchDirectory)) {
  $ScratchDirectory = Join-Path $taskRoot $ScratchDirectory
}
$ScratchDirectory = [System.IO.Path]::GetFullPath($ScratchDirectory)
$taskFull = [System.IO.Path]::GetFullPath($taskRoot)
$taskPrefix = if ($taskFull.EndsWith('\')) { $taskFull } else { $taskFull + '\' }
if (-not $ScratchDirectory.StartsWith($taskPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "ScratchDirectory must be inside the task folder: $ScratchDirectory"
}
New-Item -ItemType Directory -Force -Path $ScratchDirectory | Out-Null
$script:runDirectory = Join-Path $ScratchDirectory ('run-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $script:runDirectory | Out-Null

# ---------------------------------------------------------------------------
# Parse the candidate and dot-source only the two functions under control.
# The launcher parameter block and body are never executed.
# ---------------------------------------------------------------------------
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($CandidatePath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
$definitions = @{}
foreach ($statement in $ast.EndBlock.Statements) {
  if ($statement -is [System.Management.Automation.Language.FunctionDefinitionAst]) {
    $definitions[$statement.Name] = $statement
  }
}
$controls = @('Start-WebUi', 'Undo-StartedWebUi')
$missing = @($controls | Where-Object { -not $definitions.ContainsKey($_) })
if ($missing.Count) { throw "Candidate is missing expected functions: $($missing -join ', ')" }
foreach ($name in $controls) { . ([scriptblock]::Create($definitions[$name].Extent.Text)) }

$candidateText = [System.IO.File]::ReadAllText($CandidatePath)
$startText = $definitions['Start-WebUi'].Extent.Text
$undoText = $definitions['Undo-StartedWebUi'].Extent.Text
$nativeText = if ($definitions.ContainsKey('Invoke-NativeChecked')) { $definitions['Invoke-NativeChecked'].Extent.Text } else { '' }
$attachedText = if ($definitions.ContainsKey('Start-WebUiAttached')) { $definitions['Start-WebUiAttached'].Extent.Text } else { '' }

$script:results = [System.Collections.Generic.List[string]]::new()
function Assert-True {
  param([string]$Name, [bool]$Condition)
  if (-not $Condition) { throw "Failed control '$Name'." }
  [void]$script:results.Add($Name)
}
function Assert-Contains {
  param([string]$Name, [string]$Text, [string]$Fragment)
  if (-not $Text.Contains($Fragment)) { throw "Failed control '$Name': missing '$Fragment'." }
  [void]$script:results.Add($Name)
}
function Assert-Absent {
  param([string]$Name, [string]$Text, [string]$Fragment)
  if ($Text.Contains($Fragment)) { throw "Failed control '$Name': unexpected '$Fragment'." }
  [void]$script:results.Add($Name)
}

# ---------------------------------------------------------------------------
# Static preservation and correction controls.
# ---------------------------------------------------------------------------
Assert-Contains 'native-exit-code-guard-preserved' $nativeText 'throw "Native command failed with exit code $code."'
Assert-Contains 'native-raw-capture-preserved' $nativeText '$raw = & $application.Source @Arguments'
Assert-Absent 'no-check-then-write-pid-guard' $candidateText 'Test-PidRecordProtected'
Assert-Absent 'no-legacy-set-content-publish' $candidateText 'Set-Content -Path $pidFile'
Assert-Contains 'two-argument-no-clobber-move' $startText '[System.IO.File]::Move($stagingFile, $pidFile)'
Assert-Absent 'no-overwrite-move-overload' $startText 'Move($stagingFile, $pidFile, $true)'
Assert-Contains 'exclusive-create-new-staging' $startText '[System.IO.FileMode]::CreateNew'
$closeGuardIndex = $startText.IndexOf('publication was not attempted')
$moveIndex = $startText.IndexOf('[System.IO.File]::Move($stagingFile, $pidFile)')
Assert-True 'staging-close-failure-gates-publication' (($closeGuardIndex -ge 0) -and ($moveIndex -gt $closeGuardIndex))
Assert-Absent 'no-publication-after-close-warning' $candidateText 'Publication will still be attempted'
Assert-True 'child-start-process-errors-terminating' ($startText -match '(?s)Start-Process -FilePath \$python\.Source.*?-ErrorAction Stop')
Assert-True 'background-process-shape-preserved' (($startText.Contains('-PassThru')) -and ($startText.Contains('-WindowStyle Hidden')) -and ($startText.Contains('-RedirectStandardError $errLog')))
Assert-True 'background-no-process-kill-cmdlets' (-not ($startText -match 'Stop-Process|taskkill|Get-Process'))
Assert-Absent 'background-does-not-touch-service-record' $startText 'service.json'
$captureIndex = $startText.IndexOf('$startupError = $_')
$cleanupIndex = $startText.IndexOf('Undo-StartedWebUi -Handle')
Assert-True 'original-error-captured-before-cleanup' (($captureIndex -ge 0) -and ($cleanupIndex -gt $captureIndex))
Assert-True 'original-error-rethrown' ($startText.Contains('throw $startupError'))
Assert-Contains 'operator-message-checks-record-and-ownership' $startText 'Check that record and which service owns it before using any stop or restart lifecycle action'
Assert-Contains 'operator-message-notes-live-pid-risk' $startText 'can signal a live recorded PID'
Assert-Contains 'operator-message-distinguishes-existing-record' $startText 'An existing record is present'
Assert-Contains 'operator-message-distinguishes-general-failure' $startText 'general same-directory publication failure'
Assert-Absent 'old-stop-web-instruction-removed' $candidateText 'Clear any stale record through the existing stop_web lifecycle procedure'
Assert-Contains 'cleanup-uses-owned-process-object' $undoText 'if (-not $process.HasExited) {'
Assert-Contains 'cleanup-wait-bounded' $undoText 'WaitForExit(5000)'
Assert-Contains 'cleanup-removes-own-staging-file' $undoText 'Remove-Item -LiteralPath $Handle.staging_file'
Assert-True 'cleanup-never-removes-shared-pid-record' (-not ($undoText -match 'Remove-Item[^\r\n]*pid_file'))
Assert-True 'cleanup-no-pid-lookup-fallback' (-not ($undoText -match 'Get-Process'))
Assert-Contains 'attached-entry-preserved' $candidateText 'Start-WebUiAttached -Port $chosenPort'
Assert-Contains 'attached-direct-invocation-preserved' $attachedText '& $python.Source @pythonArgs'
Assert-True 'cli-wrapper-invocations-preserved' ([regex]::Matches($candidateText, 'Invoke-ArwCli -').Count -eq 4)
Assert-True 'single-background-entry-preserved' ([regex]::Matches($candidateText, 'Start-WebUi -Port').Count -eq 1)

# ---------------------------------------------------------------------------
# Self-contained fakes. These shadow cmdlets only in this script scope; no real
# child, browser, network call, service or process termination can occur. The
# only real I/O is the synthetic file tree under the mandatory task scratch dir.
# ---------------------------------------------------------------------------
function Reset-Fakes {
  $script:child = $null
  $script:healthMode = 'success'
  $script:warningMode = 'collect'
  $script:healthProbes = 0
  $script:warnings = [System.Collections.Generic.List[string]]::new()
}

function Get-Command {
  [CmdletBinding()]
  param([Parameter(Position = 0)][string]$Name, [string]$CommandType)
  if ($Name -ne 'python') { throw "unexpected application lookup: $Name" }
  return [pscustomobject]@{ Name = 'python'; Source = 'synthetic-python.exe' }
}

function Start-Process {
  [CmdletBinding()]
  param(
    [Parameter(Position = 0)][string]$FilePath,
    [object]$ArgumentList,
    [string]$WorkingDirectory,
    [string]$RedirectStandardOutput,
    [string]$RedirectStandardError,
    [switch]$PassThru,
    [string]$WindowStyle
  )
  if (-not $PassThru) { throw 'synthetic browser start is not expected in these controls' }
  return $script:child
}

function Start-Sleep {
  param([int]$Seconds, [int]$Milliseconds)
}

function Invoke-RestMethod {
  [CmdletBinding()]
  param(
    [Parameter(Position = 0)][string]$Uri,
    [int]$TimeoutSec,
    [int]$MaximumRedirection
  )
  $script:healthProbes = $script:healthProbes + 1
  if ($script:healthMode -ne 'success') { throw 'synthetic health probe failure' }
  return [pscustomobject]@{ status = 'ok' }
}

function Write-Warning {
  [CmdletBinding()]
  param([Parameter(Position = 0)][string]$Message)
  if ($script:warningMode -eq 'throw') { throw 'synthetic warning preference failure' }
  [void]$script:warnings.Add($Message)
}

function New-Scenario {
  param([string]$Name)
  $dir = Join-Path $script:runDirectory $Name
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  $script:ProjectRoot = $dir
  $script:HostAddress = '127.0.0.1'
  $script:OpenBrowser = $false
  $script:NoOpen = $false
  Reset-Fakes
  return $dir
}

function Test-BytesEqual {
  param([byte[]]$A, [byte[]]$B)
  if ($A.Length -ne $B.Length) { return $false }
  for ($i = 0; $i -lt $A.Length; $i++) {
    if ($A[$i] -ne $B[$i]) { return $false }
  }
  return $true
}

# ---------------------------------------------------------------------------
# 1. Fresh success: real CreateNew plus the two-argument Move publishes exactly
#    the ASCII PID and leaves the fake child running.
# ---------------------------------------------------------------------------
$freshDir = New-Scenario 'fresh-success'
$script:child = [FakeChild]::new(4242)
$freshResult = Start-WebUi -Port 8123
$freshData = Join-Path $freshDir '.research'
$freshPid = Join-Path $freshData 'web.pid'
$freshBytes = [System.IO.File]::ReadAllBytes($freshPid)
$expectedBytes = [System.Text.Encoding]::ASCII.GetBytes('4242')
Assert-True 'fresh-success-returns-running-result' (($freshResult.running -eq $true) -and ($freshResult.started -eq $true) -and ($freshResult.pid -eq 4242) -and ($freshResult.url -eq 'http://127.0.0.1:8123'))
Assert-True 'fresh-success-publishes-exact-ascii-pid' (Test-BytesEqual $freshBytes $expectedBytes)
Assert-True 'fresh-success-single-health-probe' ($script:healthProbes -eq 1)
Assert-True 'fresh-success-keeps-child-running' ($script:child.KillCount -eq 0)
Assert-True 'fresh-success-no-staging-left' (@(Get-ChildItem -LiteralPath $freshData -Filter 'web.pid.*.stage' -File).Count -eq 0)
Assert-True 'fresh-success-no-warnings' ($script:warnings.Count -eq 0)

# ---------------------------------------------------------------------------
# 2. Existing competing record: the no-clobber Move fails, the record keeps its
#    exact bytes, only the invocation's own fake child is killed and its own
#    staging file is removed.
# ---------------------------------------------------------------------------
$existingDir = New-Scenario 'existing-record'
$script:child = [FakeChild]::new(4343)
$existingData = Join-Path $existingDir '.research'
New-Item -ItemType Directory -Force -Path $existingData | Out-Null
$existingPid = Join-Path $existingData 'web.pid'
$seedBytes = [System.Text.Encoding]::ASCII.GetBytes("9999`r`n")
[System.IO.File]::WriteAllBytes($existingPid, $seedBytes)
$existingError = $null
try { $null = Start-WebUi -Port 8123 } catch { $existingError = $_.Exception.Message }
Assert-True 'existing-record-startup-fails' ($null -ne $existingError)
Assert-Contains 'existing-record-reports-publication-failure' ([string]$existingError) 'pid record could not be published'
Assert-Contains 'existing-record-guidance-checks-ownership' ([string]$existingError) 'before using any stop or restart lifecycle action'
Assert-Contains 'existing-record-guidance-notes-live-pid-risk' ([string]$existingError) 'can signal a live recorded PID'
Assert-True 'existing-record-retains-byte-identity' (Test-BytesEqual ([System.IO.File]::ReadAllBytes($existingPid)) $seedBytes)
Assert-True 'existing-record-cleans-only-own-child' (($script:child.KillCount -eq 1) -and ($script:child.WaitCount -eq 1) -and ($script:child.LastWaitTimeout -eq 5000))
Assert-True 'existing-record-cleans-own-staging' (@(Get-ChildItem -LiteralPath $existingData -Filter 'web.pid.*.stage' -File).Count -eq 0)

# ---------------------------------------------------------------------------
# 3. Readiness failure: no shared PID is written and only the fake child dies.
# ---------------------------------------------------------------------------
$readyDir = New-Scenario 'readiness-failure'
$script:child = [FakeChild]::new(4545)
$script:healthMode = 'fail'
$readyError = $null
try { $null = Start-WebUi -Port 8123 } catch { $readyError = $_.Exception.Message }
$readyData = Join-Path $readyDir '.research'
$readyPid = Join-Path $readyData 'web.pid'
Assert-Contains 'readiness-failure-reports-timeout' ([string]$readyError) 'did not become healthy within 30s'
Assert-True 'readiness-failure-probes-all-30' ($script:healthProbes -eq 30)
Assert-True 'readiness-failure-leaves-no-shared-pid' (-not (Test-Path -LiteralPath $readyPid))
Assert-True 'readiness-failure-kills-only-own-child' (($script:child.KillCount -eq 1) -and ($script:child.WaitCount -eq 1) -and ($script:child.LastWaitTimeout -eq 5000))
Assert-True 'readiness-failure-leaves-no-staging' (@(Get-ChildItem -LiteralPath $readyData -Filter 'web.pid.*.stage' -File).Count -eq 0)

# ---------------------------------------------------------------------------
# 4. Caller warning preference: a throwing warning during cleanup must not
#    replace the original startup error.
# ---------------------------------------------------------------------------
$warnDir = New-Scenario 'warning-preference'
$script:child = [FakeChild]::new(4646)
$script:child.WaitResult = $false
$script:healthMode = 'fail'
$script:warningMode = 'throw'
$warnError = $null
try { $null = Start-WebUi -Port 8123 } catch { $warnError = $_.Exception.Message }
Assert-Contains 'warning-throw-preserves-original-error' ([string]$warnError) 'did not become healthy within 30s'
Assert-Absent 'warning-throw-does-not-mask-with-warning-error' ([string]$warnError) 'synthetic warning preference failure'
Assert-True 'warning-throw-still-killed-own-child' ($script:child.KillCount -eq 1)

# ---------------------------------------------------------------------------
# Restore the caller environment and emit the machine-readable control report.
# ---------------------------------------------------------------------------
if ($null -ne $originalPythonPath) {
  $env:PYTHONPATH = $originalPythonPath
} else {
  Remove-Item -LiteralPath Env:PYTHONPATH -ErrorAction SilentlyContinue
}
[pscustomobject]@{
  candidate_path = $CandidatePath
  candidate_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $CandidatePath).Hash.ToLowerInvariant()
  scratch_directory = $script:runDirectory
  mode = 'ast-extracted-two-functions-with-fakes-and-real-synthetic-files'
  full_launcher_executed = $false
  real_child_started = $false
  network_used = $false
  services_touched = $false
  browser_started = $false
  success_exit_code = 0
  failure_exit_code = 1
  exit_code = 0
  control_count = $script:results.Count
  controls = $script:results.ToArray()
} | ConvertTo-Json -Depth 4
exit 0
