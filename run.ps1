$ErrorActionPreference = 'Stop'
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Run setup.ps1 first' }
Push-Location $PSScriptRoot
try {
    & $taskPython -m ctc_lab @args
    $taskExitCode = $LASTEXITCODE
} finally { Pop-Location }
exit $taskExitCode
