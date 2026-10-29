param([string]$PythonExe = '', [switch]$CoreOnly, [switch]$SkipModels)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
New-Item -ItemType Directory -Force -Path '.cache\tmp' | Out-Null
$env:TEMP = Join-Path $projectRoot '.cache\tmp'
$env:TMP = $env:TEMP

if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    if (-not $PythonExe) {
        throw '请用 -PythonExe 指定真实的 Python 3.12/3.13/3.14 可执行文件；不要使用失效的 WindowsApps 别名。'
    }
    & $PythonExe -c 'import sys; assert (3,12) <= sys.version_info[:2] < (3,15), "Python 3.12-3.14 required"'
    if ($LASTEXITCODE -ne 0) { throw 'Python 版本不受支持。' }
    & $PythonExe -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '创建虚拟环境失败。' }
}
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$extras = if ($CoreOnly) { '.[dev]' } else { '.[gui,catia,translation,offline,dev]' }
& $venvPython -m pip install -e $extras
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败。' }
if (-not (Test-Path -LiteralPath 'config.local.toml')) {
    Copy-Item -LiteralPath 'config.example.toml' -Destination 'config.local.toml'
}
if (-not $CoreOnly -and -not $SkipModels) {
    & $venvPython scripts/setup_translation.py
    if ($LASTEXITCODE -ne 0) { throw '离线模型安装失败，可稍后单独运行 scripts/setup_translation.py 重试。' }
}
if (-not $CoreOnly) { & (Join-Path $PSScriptRoot 'create_shortcut.ps1') }
& $venvPython -m catia_manual doctor
if ($LASTEXITCODE -ne 0) { throw '环境检查失败。' }
