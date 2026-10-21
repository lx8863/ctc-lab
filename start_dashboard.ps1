$ErrorActionPreference = 'Stop'
$taskUrl = 'http://127.0.0.1:8765'
$taskReady = $false
try {
    $taskState = Invoke-RestMethod -Uri "$taskUrl/api/state" -TimeoutSec 2
    $taskReady = ($null -ne $taskState.summary -and $null -ne $taskState.token)
} catch { }
if (-not $taskReady) {
    $taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    $taskServer = Join-Path $PSScriptRoot 'dashboard_server.py'
    if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Run setup.ps1 first' }
    $taskArtifacts = Join-Path $PSScriptRoot 'artifacts'
    New-Item -ItemType Directory -Path $taskArtifacts -Force | Out-Null
    $taskProcess = Start-Process -FilePath $taskPython -ArgumentList @("`"$taskServer`"",'--port','8765') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskArtifacts 'dashboard-server.log') -RedirectStandardError (Join-Path $taskArtifacts 'dashboard-server-error.log')
    for ($taskAttempt = 0; $taskAttempt -lt 30; $taskAttempt++) {
        Start-Sleep -Milliseconds 300
        try {
            $taskState = Invoke-RestMethod -Uri "$taskUrl/api/state" -TimeoutSec 1
            if ($null -ne $taskState.token) { $taskReady = $true; break }
        } catch { }
    }
}
if (-not $taskReady) { throw 'Cannot start dashboard. Check artifacts/dashboard-server-error.log' }
Start-Process $taskUrl
