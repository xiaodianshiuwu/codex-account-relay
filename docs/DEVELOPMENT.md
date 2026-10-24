# 开发说明

## 环境

- Python 3.9+
- PowerShell 5+（Windows 安装脚本）
- macOS 14+、Swift 5.9+（原生 GUI）
- Tk 8.5+（Tk 客户端；Linux 安装 `python3-tk`）
- PyInstaller（仅构建独立桌面包时需要）

项目没有运行时第三方 Python 依赖；标准库之外的可选构建依赖见 `THIRD_PARTY_NOTICES.md`。

## 目录

```text
codex_relay/        Python 核心、CLI、Tk 客户端、本地化
desktop/            macOS SwiftUI 应用
portable/           Tk 桌面打包脚本
tools/              构建与 i18n 审计工具
tests/              Python 单元/集成测试
docs/               用户与开发者文档
i18n/               翻译源与审计 allowlist
```

## 测试

```sh
python3 tests/run.py
python3 -m codex_relay.portable_gui --self-test
swift test --package-path desktop --scratch-path /tmp/codex-relay-swift
python3 tools/build_strings.py --check
python3 tools/i18n_audit.py --json
```

测试使用隔离的 `CODEX_HOME`/`CODEX_RELAY_HOME`，并强制 `CODEX_RELAY_TEST_MODE=1`；不要用真实账号运行写操作测试。

## 构建

```sh
python3 -m pip wheel . --no-deps -w dist-cli
python3 -m pip install pyinstaller
python3 tools/build_portable.py
```

macOS 原生 App：

```sh
cd desktop
./build.sh
```

跨平台产物必须在目标系统构建；PyInstaller 不能从 macOS 交叉生成 Windows/Linux 可执行文件。

## 代码约定

- 文件路径使用 `pathlib`；不要硬编码开发者主目录。
- 所有用户可见文案走 `i18n/` 或 `Translator`，新增键必须四语言齐全。
- 凭据、token、auth 文件和会话内容不得写入日志、测试 fixture 或提交。
- 进程控制必须保留显式确认和测试模式硬保护。

## 贡献

提交前运行完整测试和 `git diff --check`，不要提交 `.env`、`auth.json`、数据库、日志、截图或构建产物。见 `CONTRIBUTING.md`。
