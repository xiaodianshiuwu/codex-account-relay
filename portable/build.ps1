$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python = if ($env:PYTHON) { $env:PYTHON } else { "python" }
& $Python "$Root\tools\build_portable.py"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 构建失败" }
Write-Host "产物: $Root\dist\CodexRelay.exe"
