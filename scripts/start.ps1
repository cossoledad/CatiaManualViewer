param([string]$Config = '')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$pythonWindowless = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonWindowless)) { throw '请先执行 scripts\bootstrap.ps1。' }
$arguments = '-m catia_manual.gui.app'
if ($Config) { $arguments += ' --config "' + $Config + '"' }
Start-Process -FilePath $pythonWindowless -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden
