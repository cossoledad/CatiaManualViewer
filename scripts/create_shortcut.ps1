$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonWindowless = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonWindowless)) { throw '请先创建项目虚拟环境。' }
$shortcutPath = Join-Path $projectRoot 'Catia Manual Viewer.lnk'
$shellObject = New-Object -ComObject WScript.Shell
$shortcut = $shellObject.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonWindowless
$shortcut.Arguments = '-m catia_manual.gui.app'
$shortcut.WorkingDirectory = $projectRoot
$shortcut.IconLocation = Join-Path $projectRoot 'src\catia_manual\assets\app.ico'
$shortcut.Description = 'Catia Manual Viewer'
$shortcut.Save()
Write-Output $shortcutPath
