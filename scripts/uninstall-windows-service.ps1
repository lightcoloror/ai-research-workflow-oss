param(
  [ValidateSet("Nssm", "ScheduledTask")]
  [string]$Mode = "Nssm",
  [string]$ServiceName = "ai-research-workflow",
  [string]$NssmPath = "nssm.exe"
)

$ErrorActionPreference = "Stop"

function Test-CommandExists([string]$Command) {
  return $null -ne (Get-Command $Command -ErrorAction SilentlyContinue)
}

if ($Mode -eq "Nssm") {
  if (-not (Test-CommandExists $NssmPath)) {
    throw "NSSM was not found at '$NssmPath'."
  }
  & $NssmPath stop $ServiceName
  if ($LASTEXITCODE -ne 0) {
    throw "NSSM stop failed with exit code $LASTEXITCODE."
  }
  & $NssmPath remove $ServiceName confirm
  if ($LASTEXITCODE -ne 0) {
    throw "NSSM remove failed with exit code $LASTEXITCODE."
  }
} else {
  Stop-ScheduledTask -TaskName $ServiceName -ErrorAction SilentlyContinue
  Unregister-ScheduledTask -TaskName $ServiceName -Confirm:$false
}

[pscustomobject]@{
  service_name = $ServiceName
  mode = $Mode
  removed = $true
} | ConvertTo-Json -Depth 3
