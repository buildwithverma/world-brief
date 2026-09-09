$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$assistantPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $assistantPython)) { throw 'Python dependencies are missing. Follow README.md to install them.' }
& $assistantPython (Join-Path $PSScriptRoot 'scripts\run.py')
