[CmdletBinding()]
param(
  [Parameter(Mandatory = $true, Position = 0)]
  [ValidateSet("route", "search", "deepen")]
  [string]$Command,

  [Parameter(Mandatory = $true, Position = 1)]
  [string]$Query,

  [ValidateSet("auto", "daily", "academic", "tool_selection")]
  [string]$Mode = "auto",

  [ValidateSet("default", "anti_seo", "technical_primary", "medical_high_risk", "policy_primary", "product_feedback")]
  [string]$Profile = "",

  [ValidateSet("adaptive_staged", "legacy_fanout", "manual")]
  [string]$RoutingStrategy = "",

  [int]$MaxResults = 0,
  [string[]]$Provider = @(),
  [switch]$NoNetwork,
  [switch]$Fetch,
  [ValidateSet("json", "md")]
  [string]$Format = "json",
  [string]$ConfigPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $ConfigPath) {
  $ConfigPath = Join-Path $ProjectRoot "research.config.json"
}
if (-not (Test-Path -LiteralPath $ConfigPath)) {
  throw "Research config not found: $ConfigPath"
}

$config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$effectiveProfile = if ($Profile) { $Profile } elseif ($config.search_profile) { [string]$config.search_profile } else { "default" }
$effectiveStrategy = if ($Provider.Count -gt 0) { "manual" } elseif ($RoutingStrategy) { $RoutingStrategy } elseif ($config.routing_strategy) { [string]$config.routing_strategy } else { "legacy_fanout" }
$effectiveMaxResults = if ($MaxResults -gt 0) { $MaxResults } elseif ($config.max_results) { [int]$config.max_results } else { 10 }
$baseUrl = "http://$($config.web_host):$([int]$config.web_port)"

$dataDir = [string]$config.data_dir
if (-not [System.IO.Path]::IsPathRooted($dataDir)) {
  $dataDir = Join-Path $ProjectRoot $dataDir
}
$serviceRecordPath = Join-Path $dataDir "service.json"
$serviceRecord = $null
if (Test-Path -LiteralPath $serviceRecordPath) {
  try {
    $serviceRecord = Get-Content -LiteralPath $serviceRecordPath -Raw -Encoding UTF8 | ConvertFrom-Json
  } catch {
    $serviceRecord = $null
  }
}

function Test-ResearchHealth {
  try {
    $health = Invoke-RestMethod -Uri "$baseUrl/v1/health" -Method Get -TimeoutSec 2
    return ($health.status -eq "ok" -and $health.service -eq "ai-research-workflow")
  } catch {
    return $false
  }
}

function Add-TransportMetadata {
  param([object]$Result, [string]$Transport, [string]$Reason)
  if ($Result -is [System.Collections.IDictionary]) {
    $Result["transport"] = $Transport
    $Result["transport_reason"] = $Reason
    $Result["configured_base_url"] = $baseUrl
    $Result["service_record_status"] = if ($serviceRecord) { [string]$serviceRecord.status } else { "missing" }
    $Result["http_health_observed"] = $healthy
    return ($Result | ConvertTo-Json -Depth 50)
  }
  $Result | Add-Member -NotePropertyName transport -NotePropertyValue $Transport -Force
  $Result | Add-Member -NotePropertyName transport_reason -NotePropertyValue $Reason -Force
  $Result | Add-Member -NotePropertyName configured_base_url -NotePropertyValue $baseUrl -Force
  $Result | Add-Member -NotePropertyName service_record_status -NotePropertyValue $(if ($serviceRecord) { [string]$serviceRecord.status } else { "missing" }) -Force
  $Result | Add-Member -NotePropertyName http_health_observed -NotePropertyValue $healthy -Force
  return ($Result | ConvertTo-Json -Depth 50)
}

function Invoke-ResearchCli {
  $cliArgs = @("-B", "-m", "research_workflow.cli", "--config", $ConfigPath, $Command, $Query, "--mode", $Mode, "--profile", $effectiveProfile, "--routing-strategy", $effectiveStrategy, "--format", $Format)
  if ($Command -ne "route") {
    $cliArgs += @("--max-results", [string]$effectiveMaxResults)
    if ($NoNetwork) { $cliArgs += "--no-network" }
    if ($Fetch) { $cliArgs += "--fetch" }
  }
  foreach ($name in $Provider) {
    $cliArgs += @("--provider", $name)
  }
  Push-Location $ProjectRoot
  try {
    $output = & python @cliArgs
    if ($LASTEXITCODE -ne 0) {
      throw "Research CLI exited with code $LASTEXITCODE"
    }
  } finally {
    Pop-Location
  }
  if ($Format -eq "md") {
    $output -join [Environment]::NewLine
    return
  }
  $result = ($output -join [Environment]::NewLine) | ConvertFrom-Json
  Add-TransportMetadata -Result $result -Transport "cli" -Reason "CLI selected before POST: health unavailable or md-format request"
}

$healthy = Test-ResearchHealth
if (-not $healthy -or $Format -eq "md") {
  Invoke-ResearchCli
  exit 0
}

$payload = @{
  topic = $Query
  mode = $Mode
  profile = $effectiveProfile
  routing_strategy = $effectiveStrategy
}
if ($Provider.Count -gt 0) { $payload.providers = $Provider }
if ($Command -ne "route") {
  $payload.max_results = $effectiveMaxResults
  $payload.no_network = [bool]$NoNetwork
  $payload.fetch = [bool]$Fetch
}
$path = if ($Command -eq "route") { "/v1/route" } elseif ($Command -eq "deepen") { "/v1/deepen" } else { "/v1/search" }
try {
  $result = Invoke-RestMethod -Uri "$baseUrl$path" -Method Post -ContentType "application/json" -Body ($payload | ConvertTo-Json -Depth 20) -TimeoutSec 180
  Add-TransportMetadata -Result $result -Transport "http" -Reason "configured service passed /v1/health"
} catch {
  # POST already submitted with original payload/timeout; outcome unknown.
  # Do not auto-fallback to CLI to avoid potential duplicate execution of the same request.
  throw
}
