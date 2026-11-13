param(
  [string]$HostAddress = "",
  [ValidateRange(0, 65535)]
  [int]$PreferredPort = 0,
  [string]$PortCheckScript,
  [string]$SearxngHealthScript,
  [string]$SearxngStartScript,
  [string]$PortRecord,
  [switch]$Background,
  [switch]$ManageSearxng,
  [switch]$UpdateProjectConfig,
  [switch]$OpenBrowser,
  [switch]$NoOpen
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ProjectConfigPath = Join-Path $ProjectRoot "research.config.json"

function Invoke-NativeChecked {
  param([string]$Program, [string[]]$Arguments)
  $application = Get-Command -Name $Program -CommandType Application -ErrorAction Stop | Select-Object -First 1
  $raw = & $application.Source @Arguments
  $code = $LASTEXITCODE
  if ($code -ne 0) {
    throw "Native command failed with exit code $code."
  }
  return $raw
}

function Invoke-JsonCommand {
  param([string]$Program, [string[]]$Arguments)
  $text = (Invoke-NativeChecked -Program $Program -Arguments $Arguments | Out-String).Trim()
  if (-not $text.StartsWith("{") -or -not $text.EndsWith("}")) {
    throw "Expected one JSON object from the native command."
  }
  try {
    $value = ConvertFrom-Json -InputObject $text -ErrorAction Stop
  } catch {
    throw "Native command returned invalid JSON."
  }
  if ($value -isnot [pscustomobject]) {
    throw "Expected one JSON object from the native command."
  }
  return $value
}

function Invoke-ArwCli {
  param(
    [string[]]$Arguments,
    [switch]$AsJson
  )
  $pythonPathExisted = Test-Path Env:PYTHONPATH
  $previousPythonPath = $env:PYTHONPATH
  $srcPath = Join-Path $ProjectRoot "src"
  if ($previousPythonPath) {
    $env:PYTHONPATH = "$srcPath;$previousPythonPath"
  } else {
    $env:PYTHONPATH = $srcPath
  }
  try {
    if ($AsJson) {
      return Invoke-JsonCommand -Program python -Arguments $Arguments
    }
    return Invoke-NativeChecked -Program python -Arguments $Arguments
  } finally {
    if ($pythonPathExisted) {
      $env:PYTHONPATH = $previousPythonPath
    } else {
      Remove-Item -LiteralPath Env:PYTHONPATH -ErrorAction SilentlyContinue
    }
  }
}

function Get-ProjectConfig {
  Push-Location $ProjectRoot
  try {
    return Invoke-ArwCli -AsJson -Arguments @("-m", "research_workflow.cli", "config")
  } finally {
    Pop-Location
  }
}

function Sync-ProjectWebConfig {
  param(
    [string]$WebHost,
    [int]$Port
  )
  if (-not (Test-Path -LiteralPath $ProjectConfigPath)) {
    Push-Location $ProjectRoot
    try {
      Invoke-ArwCli -Arguments @("-m", "research_workflow.cli", "config", "--init") | Out-Null
    } finally {
      Pop-Location
    }
  }
  $config = Get-Content -LiteralPath $ProjectConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
  $changed = $false
  if ([string]$config.web_host -ne $WebHost) {
    $config.web_host = $WebHost
    $changed = $true
  }
  if ([int]$config.web_port -ne $Port) {
    $config.web_port = $Port
    $changed = $true
  }
  if ($changed) {
    $config | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $ProjectConfigPath -Encoding UTF8
    Write-Host "Updated project Web config: $ProjectConfigPath -> http://${WebHost}:$Port"
    Push-Location $ProjectRoot
    try {
      Invoke-ArwCli -Arguments @(
        "-m", "research_workflow.cli", "integrations", "--output", "integrations", "--format", "json"
      ) | Out-Null
      Write-Host "Regenerated integrations from project config."
    } finally {
      Pop-Location
    }
  }
}

function Ensure-NativeSearxng {
  if (-not $ManageSearxng) {
    return
  }
  $health = Invoke-JsonCommand -Program powershell.exe -Arguments @("-NoProfile", "-File", $SearxngHealthScript)
  if ($health.status -is [string] -and $health.status -eq "ok") {
    Write-Host "Native SearXNG OK."
    return
  }
  if ($health.status -isnot [string] -or $health.status -notin @("unhealthy", "stopped", "not_running", "not_ready", "offline")) {
    throw "SearXNG health helper did not return a recognized status."
  }
  if (-not $SearxngStartScript) {
    throw "SearXNG is unhealthy and no valid start script is configured."
  }
  $started = Invoke-JsonCommand -Program powershell.exe -Arguments @("-NoProfile", "-File", $SearxngStartScript)
  if ($started.status -isnot [string] -or $started.status -notin @("started", "already_listening", "starting")) {
    throw "Native SearXNG failed to start."
  }
  Write-Host "Native SearXNG $($started.status)."
}

function Get-WebStatus {
  param([int]$Port)
  Push-Location $ProjectRoot
  try {
    return Invoke-ArwCli -AsJson -Arguments @(
      "-m", "research_workflow.cli", "web", "status", "--host", $HostAddress, "--port", "$Port", "--format", "json"
    )
  } finally {
    Pop-Location
  }
}

function Test-WebHealth {
  param([string]$Url)
  try {
    Invoke-RestMethod -Uri "$Url/v1/health" -TimeoutSec 2 -MaximumRedirection 0 | Out-Null
    return $true
  } catch {
    return $false
  }
}

function Get-ExistingServiceStatus {
  param([string]$ExpectedUrl)
  $servicePath = Join-Path (Join-Path $ProjectRoot ".research") "service.json"
  if (-not (Test-Path -LiteralPath $servicePath)) {
    return $null
  }
  try {
    $service = Get-Content -LiteralPath $servicePath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $service.pid -or -not $service.base_url -or $service.status -eq "stopped") {
      return $null
    }
    if ($ExpectedUrl -and ([string]$service.base_url).TrimEnd("/") -ne $ExpectedUrl.TrimEnd("/")) {
      return $null
    }
    $process = Get-Process -Id ([int]$service.pid) -ErrorAction SilentlyContinue
    if (-not $process) {
      return $null
    }
    if (-not (Test-WebHealth -Url ([string]$service.base_url))) {
      return $null
    }
    return [pscustomobject]@{
      running = $true
      started = $false
      pid = [int]$service.pid
      url = [string]$service.base_url
      service_record_path = $servicePath
    }
  } catch {
    return $null
  }
}

function Undo-StartedWebUi {
  param($Handle, [string]$Reason)
  if (-not $Handle) {
    return $true
  }
  # Cleanup is limited to this invocation's child Process object and this
  # invocation's own staging file. The shared pid record is never touched:
  # the existing Python lifecycle (start_web/stop_web/status) owns it.
  $cleanupOk = $true
  $processId = $null
  if ($Handle.process) {
    $process = $Handle.process
    $processId = $process.Id
    try {
      if (-not $process.HasExited) {
        $process.Kill()
        if (-not $process.WaitForExit(5000)) {
          $cleanupOk = $false
        }
      }
      if (-not $process.HasExited) {
        $cleanupOk = $false
      }
    } catch {
      $cleanupOk = $false
    }
  }
  if ($Handle.staging_created -and $Handle.staging_file) {
    try {
      if (Test-Path -LiteralPath $Handle.staging_file) {
        Remove-Item -LiteralPath $Handle.staging_file -Force -ErrorAction Stop
      }
    } catch {
      $cleanupOk = $false
    }
  }
  if (-not $cleanupOk) {
    $pidText = if ($null -ne $processId) { "$processId" } else { "unknown" }
    Write-Warning "Could not fully clean up the background Web UI child process pid=$pidText after $Reason; inspect logs '$($Handle.stderr_log)'. The shared pid record '$($Handle.pid_file)' was left untouched."
  }
  return $cleanupOk
}

function Start-WebUi {
  param([int]$Port)
  $python = Get-Command -Name python -CommandType Application -ErrorAction Stop | Select-Object -First 1
  $dataDir = Join-Path $ProjectRoot ".research"
  $pidFile = Join-Path $dataDir "web.pid"
  $outLog = Join-Path $dataDir "web.out.log"
  $errLog = Join-Path $dataDir "web.err.log"
  New-Item -ItemType Directory -Force -Path $dataDir | Out-Null

  $pythonArgs = @("-m", "research_workflow.web", "--host", $HostAddress, "--port", "$Port", "--data-dir", ".research")
  $pythonPathExisted = Test-Path Env:PYTHONPATH
  $previousPythonPath = $env:PYTHONPATH
  $srcPath = Join-Path $ProjectRoot "src"
  if ($previousPythonPath) {
    $env:PYTHONPATH = "$srcPath;$previousPythonPath"
  } else {
    $env:PYTHONPATH = $srcPath
  }
  $stagingFile = Join-Path $dataDir ("web.pid.{0}.{1}.stage" -f $PID, [guid]::NewGuid().ToString("N"))
  $handle = [pscustomobject]@{
    process = $null
    pid_file = $pidFile
    staging_file = $stagingFile
    staging_created = $false
    stdout_log = $outLog
    stderr_log = $errLog
  }
  try {
    try {
      $process = Start-Process -FilePath $python.Source `
        -ArgumentList $pythonArgs `
        -WorkingDirectory $ProjectRoot `
        -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog `
        -PassThru `
        -WindowStyle Hidden `
        -ErrorAction Stop
      # Capture ownership before the environment restore below can throw and
      # strand the child process without a cleanup handle.
      $handle.process = $process
    } finally {
      if ($pythonPathExisted) {
        $env:PYTHONPATH = $previousPythonPath
      } else {
        Remove-Item -LiteralPath Env:PYTHONPATH -ErrorAction SilentlyContinue
      }
    }
    $url = "http://${HostAddress}:$Port"
    $healthUrl = "$url/v1/health"
    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
      Start-Sleep -Seconds 1
      if ($process.HasExited) {
        break
      }
      try {
        Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2 -MaximumRedirection 0 | Out-Null
        $ready = $true
        break
      } catch {
        $process.Refresh()
      }
    }
    if (-not $ready) {
      $status = if ($process.HasExited) { "exited" } else { "starting" }
      throw "Web UI did not become healthy within 30s; status=$status; pid=$($process.Id); stderr=$errLog"
    }
    # Final health confirmation has succeeded. Build the result before the
    # fallible publication step so that nothing after publication can convert
    # a running, published service into a reported failure.
    $result = [pscustomobject]@{
      running = $true
      started = $true
      pid = $process.Id
      url = $url
      command = @("python") + $pythonArgs
      pid_file = $pidFile
      stdout_log = $outLog
      stderr_log = $errLog
    }
    $pidText = "$($process.Id)"
    $pidBytes = [System.Text.Encoding]::ASCII.GetBytes($pidText)
    $stagingStream = $null
    try {
      $stagingStream = [System.IO.File]::Open(
        $stagingFile,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None
      )
      $handle.staging_created = $true
    } catch {
      throw "Could not create the per-invocation pid staging file '$stagingFile'; the shared pid record '$pidFile' was not changed. $($_.Exception.Message)"
    }
    $stagingCloseFailure = $null
    try {
      $stagingStream.Write($pidBytes, 0, $pidBytes.Length)
      $stagingStream.Flush($true)
    } catch {
      throw "Could not write the per-invocation pid staging file '$stagingFile'; the shared pid record '$pidFile' was not changed. $($_.Exception.Message)"
    } finally {
      try {
        $stagingStream.Dispose()
      } catch {
        # A failed close leaves the staged bytes unverified, so publication
        # must not be attempted. The outer handler removes this invocation's
        # staging file; no shared pid record has been touched.
        $stagingCloseFailure = $_.Exception.Message
      }
    }
    if ($null -ne $stagingCloseFailure) {
      throw "Could not close the per-invocation pid staging file '$stagingFile'; publication was not attempted and the shared pid record '$pidFile' was not changed. $stagingCloseFailure"
    }
    try {
      # Two-argument System.IO.File.Move is the no-overwrite publication
      # primitive on Windows PowerShell 5.1: it fails if the destination
      # exists instead of replacing it. The staged payload is a plain decimal
      # PID (ASCII, no BOM, no newline).
      [System.IO.File]::Move($stagingFile, $pidFile)
    } catch {
      # The move is the single atomic no-clobber step, so the check below is
      # only a post-failure observation for the operator message; publication
      # itself never uses a check-then-write decision.
      $publicationError = $_
      $recordPresent = $false
      try { $recordPresent = Test-Path -LiteralPath $pidFile } catch { $recordPresent = $false }
      $guidance = if ($recordPresent) {
        "An existing record is present at '$pidFile' and its bytes were left unchanged. Check that record and which service owns it before using any stop or restart lifecycle action; the existing Python stop_web procedure can signal a live recorded PID."
      } else {
        "No record is present at '$pidFile', so this is a general same-directory publication failure rather than an existing-record conflict."
      }
      throw "The Web UI is healthy (pid=$pidText) but its pid record could not be published; the shared pid record was not modified. $guidance $($publicationError.Exception.Message)"
    }
  } catch {
    $startupError = $_
    try {
      $null = Undo-StartedWebUi -Handle $handle -Reason "a background startup failure"
    } catch {
      $cleanupError = $_
      # Warning emission can itself be terminating when the caller prefers
      # warnings as errors; never let it replace the original startup error.
      try {
        Write-Warning "Cleanup after a background startup failure raised an additional error: $($cleanupError.Exception.Message)"
      } catch {
      }
    }
    throw $startupError
  }
  if ($OpenBrowser -and -not $NoOpen) {
    try {
      Start-Process $url -ErrorAction Stop
    } catch {
      Write-Warning -WarningAction Continue "The background Web UI is healthy and its pid record was published, but the browser could not be opened: $($_.Exception.Message)"
    }
  }
  return $result
}

function Start-WebUiAttached {
  param([int]$Port)
  $python = Get-Command -Name python -CommandType Application -ErrorAction Stop | Select-Object -First 1
  $dataDir = Join-Path $ProjectRoot ".research"
  New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
  $pythonArgs = @("-m", "research_workflow.web", "--host", $HostAddress, "--port", "$Port", "--data-dir", ".research")
  $url = "http://${HostAddress}:$Port"
  $result = [pscustomobject]@{
    running = $true
    started = $true
    attached = $true
    pid = $PID
    url = $url
    command = @("python") + $pythonArgs
    data_dir = $dataDir
  }
  Add-PortRecord -Result $result
  Write-Host "Research Workflow UI starting in this PowerShell window: $url"
  Write-Host "Keep this window open while using the UI. Press Ctrl+C to stop it."
  if ($OpenBrowser -and -not $NoOpen) {
    Start-Process $url
  }
  Push-Location $ProjectRoot
  $pythonPathExisted = Test-Path Env:PYTHONPATH
  $previousPythonPath = $env:PYTHONPATH
  $srcPath = Join-Path $ProjectRoot "src"
  if ($previousPythonPath) {
    $env:PYTHONPATH = "$srcPath;$previousPythonPath"
  } else {
    $env:PYTHONPATH = $srcPath
  }
  try {
    & $python.Source @pythonArgs
  } finally {
    if ($pythonPathExisted) {
      $env:PYTHONPATH = $previousPythonPath
    } else {
      Remove-Item -LiteralPath Env:PYTHONPATH -ErrorAction SilentlyContinue
    }
    Pop-Location
  }
}

function Test-PortBindable {
  param([int]$Port)
  $python = Get-Command -Name python -CommandType Application -ErrorAction Stop | Select-Object -First 1
  $script = @"
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        pass
try:
    server = ThreadingHTTPServer(("127.0.0.1", $Port), Handler)
except OSError:
    raise SystemExit(1)
server.server_close()
"@
  $script | & $python.Source -
  return $LASTEXITCODE -eq 0
}

function Test-PortRecordAvailable {
  param([int]$Port)
  if (-not $PortRecord -or -not (Test-Path -LiteralPath $PortRecord)) {
    return $true
  }
  $recordText = Get-Content -LiteralPath $PortRecord -Raw -Encoding UTF8
  return (-not $recordText.Contains(":$Port")) -and (-not $recordText.Contains("| $Port")) -and (-not $recordText.Contains(" $Port "))
}

function Test-RecordedPortReusableForThisProject {
  param([int]$Port)
  if (-not $PortRecord -or -not (Test-Path -LiteralPath $PortRecord)) {
    return $true
  }
  $portPattern = "(^|[^\d])$Port([^\d]|$)"
  $matchingLines = @(Get-Content -LiteralPath $PortRecord -Encoding UTF8 | Where-Object { $_ -match $portPattern })
  if ($matchingLines.Count -eq 0) {
    return $true
  }
  foreach ($line in $matchingLines) {
    if ($line -notmatch "ai-research-workflow") {
      return $false
    }
  }
  return $true
}

function Find-BindablePort {
  param([int]$StartPort)
  if ($StartPort -lt 1 -or $StartPort -gt 65535) {
    throw "No valid fallback port remains."
  }
  $lastPort = [Math]::Min(65535, ($StartPort + 199))
  for ($port = $StartPort; $port -le $lastPort; $port++) {
    if ((Test-PortRecordAvailable -Port $port) -and (Test-PortBindable -Port $port)) {
      return $port
    }
  }
  throw "No bindable localhost port found from $StartPort to $lastPort."
}

function Add-PortRecord {
  param($Result)
  if (-not $PortRecord -or -not (Test-Path -LiteralPath $PortRecord)) {
    return
  }
  $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  $pidText = if ($Result.pid) { "$($Result.pid)" } else { "unknown" }
  $line = "`n- $timestamp | local launcher | ai-research-workflow Web UI | $($Result.url) | PID $pidText | start-ui.ps1"
  Add-Content -LiteralPath $PortRecord -Value $line -Encoding UTF8
}

if ($OpenBrowser -and $NoOpen) {
  throw "Choose either -OpenBrowser or -NoOpen."
}
if (($SearxngHealthScript -or $SearxngStartScript) -and -not $ManageSearxng) {
  throw "SearXNG helper paths require -ManageSearxng."
}
if ($ManageSearxng -and -not $SearxngHealthScript) {
  throw "-ManageSearxng requires -SearxngHealthScript."
}
foreach ($helper in @($PortCheckScript, $SearxngHealthScript, $SearxngStartScript)) {
  if ($helper -and -not (Test-Path -LiteralPath $helper -PathType Leaf)) {
    throw "A specified local helper script is unavailable."
  }
}
if ($PortRecord -and -not (Test-Path -LiteralPath $PortRecord -PathType Leaf)) {
  throw "The specified port record is unavailable."
}

$ProjectConfig = Get-ProjectConfig
if (-not $HostAddress) {
  $HostAddress = [string]$ProjectConfig.web_host
}
if ($PreferredPort -le 0) {
  $PreferredPort = [int]$ProjectConfig.web_port
}
if ($HostAddress -notin @("127.0.0.1", "localhost") -or $PreferredPort -lt 1 -or $PreferredPort -gt 65535) {
  throw "Choose a loopback host (127.0.0.1 or localhost) and a port from 1 to 65535."
}

Ensure-NativeSearxng

$expectedBaseUrl = "http://${HostAddress}:$PreferredPort"
$existingService = Get-ExistingServiceStatus -ExpectedUrl $expectedBaseUrl
if ($existingService) {
  Write-Host "Research Workflow UI already running: $($existingService.url)"
  if ($OpenBrowser -and -not $NoOpen) {
    Start-Process $existingService.url
  }
  $existingService | ConvertTo-Json -Depth 8
  exit 0
}

$status = Get-WebStatus -Port $PreferredPort
if ($status.running -isnot [bool]) {
  throw "Web status did not return a boolean running field."
}
if ($status.running -and ([string]$status.url).TrimEnd("/") -ne $expectedBaseUrl) {
  throw "Web status URL does not match the requested loopback URL."
}
if ($status.running -and (Test-WebHealth -Url $expectedBaseUrl)) {
  Write-Host "Research Workflow UI already running: $($status.url)"
  if ($OpenBrowser -and -not $NoOpen) {
    Start-Process $expectedBaseUrl
  }
  $status | ConvertTo-Json -Depth 8
  exit 0
} elseif ($status.running -eq $true) {
  Write-Host "Ignoring stale Web UI pid $($status.pid); health check failed for $($status.url)."
}

$chosenPort = $PreferredPort
if ($PortCheckScript) {
  $preflight = Invoke-JsonCommand -Program powershell.exe -Arguments @(
    "-NoProfile", "-File", $PortCheckScript, "-PreferredPort", "$PreferredPort", "-ServiceName", "ai-research-workflow-ui"
  )
  if ($preflight.available -isnot [bool]) {
    throw "Port helper did not return a boolean available field."
  }
  if (-not $preflight.available) {
    $recordOnlySelfConflict = ($preflight.recorded_conflict -eq $true) -and ($preflight.live_conflict -ne $true) -and ($preflight.excluded_conflict -ne $true) -and (Test-RecordedPortReusableForThisProject -Port $PreferredPort)
    if ($recordOnlySelfConflict) {
      Write-Host "Preferred port $PreferredPort only appears in this project's historical port records and has no live listener; reusing configured port."
    } else {
      $startPort = [Math]::Max(([int]$preflight.suggested_port), ($PreferredPort + 1))
      $chosenPort = Find-BindablePort -StartPort $startPort
      Write-Host "Preferred port $PreferredPort unavailable by port record/live check; using $chosenPort."
    }
  }
}

if (-not (Test-PortBindable -Port $chosenPort)) {
  $bindablePort = Find-BindablePort -StartPort ($chosenPort + 1)
  Write-Host "Port $chosenPort passed record check but failed socket bind; using $bindablePort."
  $chosenPort = $bindablePort
}

if ($UpdateProjectConfig -and ($chosenPort -ne $PreferredPort -or [string]$ProjectConfig.web_host -ne $HostAddress)) {
  Sync-ProjectWebConfig -WebHost $HostAddress -Port $chosenPort
}

if (-not $Background) {
  Start-WebUiAttached -Port $chosenPort
  exit $LASTEXITCODE
}

$result = Start-WebUi -Port $chosenPort
try {
  Add-PortRecord -Result $result
} catch {
  Write-Warning -WarningAction Continue "The background Web UI is healthy and its pid record was published, but the optional port record could not be updated: $($_.Exception.Message)"
}
$result | ConvertTo-Json -Depth 8
