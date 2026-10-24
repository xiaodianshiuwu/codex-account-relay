# Codex Relay Windows installer (PowerShell 5+).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Share = if ($env:CODEX_RELAY_SHARE) { $env:CODEX_RELAY_SHARE } else { Join-Path $HOME ".local\share\codex-relay" }
$BinDir = if ($env:CODEX_RELAY_BIN_DIR) { $env:CODEX_RELAY_BIN_DIR } else { Join-Path $HOME ".local\bin" }
$Python = if ($env:CODEX_RELAY_PYTHON) { $env:CODEX_RELAY_PYTHON } else { "python" }

New-Item -ItemType Directory -Force -Path $Share, $BinDir | Out-Null
Remove-Item -Recurse -Force (Join-Path $Share "codex_relay") -ErrorAction SilentlyContinue
Copy-Item -Recurse -Force (Join-Path $Root "codex_relay") (Join-Path $Share "codex_relay")
$resources = Join-Path $Root "desktop\Resources"
if (-not (Test-Path $resources)) { $resources = Join-Path $Root "Resources" }
if (Test-Path $resources) {
  Remove-Item -Recurse -Force (Join-Path $Share "Resources") -ErrorAction SilentlyContinue
  Copy-Item -Recurse -Force $resources (Join-Path $Share "Resources")
}
if (Test-Path (Join-Path $Root "README.md")) {
  Copy-Item -Force (Join-Path $Root "README.md") (Join-Path $Share "README.md")
}
$launcher = @"
@echo off
set "PYTHONPATH=$Share;%PYTHONPATH%"
"$Python" -m codex_relay %*
"@
Set-Content -Encoding Default -Path (Join-Path $BinDir "codex-relay.cmd") -Value $launcher
$guiLauncher = @"
@echo off
set "PYTHONPATH=$Share;%PYTHONPATH%"
"$Python" -m codex_relay.portable_gui %*
"@
Set-Content -Encoding Default -Path (Join-Path $BinDir "codex-relay-gui.cmd") -Value $guiLauncher
Write-Host "已安装: $BinDir\codex-relay.cmd"
Write-Host "图形界面: $BinDir\codex-relay-gui.cmd"
Write-Host "请将 $BinDir 加入 PATH（当前用户 PATH 可通过系统设置修改）。"
Write-Host "数据目录: $HOME\.codex-relay"
