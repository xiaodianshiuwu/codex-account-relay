# 开发说明

## 环境

- macOS 14+、Swift 5.9+（唯一支持的 SwiftUI App）
- Python 3.9+（内部 CLI 后端与测试）
- PyInstaller（构建 App 内置后端时需要）

Windows/Linux/Tk 代码仍保留在仓库中，但不是公开支持范围；本文不提供其构建或验收承诺。

项目没有运行时第三方 Python 依赖；标准库之外的可选构建依赖见 `THIRD_PARTY_NOTICES.md`。

## 目录

```text
codex_relay/        Python 核心、CLI、历史 Tk 客户端、本地化
desktop/            macOS SwiftUI 应用（唯一发布形态）
portable/           历史 Tk 打包脚本（不发布）
tools/              构建与 i18n 审计工具
tests/              Python 单元/集成测试
docs/               用户与开发者文档
i18n/               翻译源与审计 allowlist
```

## 测试

```sh
python3 tests/run.py
swift test --package-path desktop --scratch-path /tmp/codex-relay-swift
python3 tools/build_strings.py --check
python3 tools/i18n_audit.py --json
```

测试使用隔离的 `CODEX_HOME`/`CODEX_RELAY_HOME`，并强制 `CODEX_RELAY_TEST_MODE=1`；不要用真实账号运行写操作测试。

## 构建

```sh
python3 -m pip install pyinstaller
python3 tools/build_cli_bundle.py
cd desktop
./build.sh
```

发布流程会把 `dist-cli/codex-relay` 复制进 App 的 `Contents/Resources`；本地源码构建没有该文件时回退到 PATH 中的 `codex-relay`。Windows/Linux/Tk 产物不再发布。

## 代码约定

- 文件路径使用 `pathlib`；不要硬编码开发者主目录。
- 所有用户可见文案走 `i18n/` 或 `Translator`，新增键必须四语言齐全。
- 凭据、token、auth 文件和会话内容不得写入日志、测试 fixture 或提交。
- 进程控制必须保留显式确认和测试模式硬保护。

## 贡献

提交前运行完整测试和 `git diff --check`，不要提交 `.env`、`auth.json`、数据库、日志、截图或构建产物。见 `CONTRIBUTING.md`。
