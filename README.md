# Codex Relay

Codex Relay 是一个 **Codex 多账号接力工具**：把不同 ChatGPT/Codex 账号的凭据隔离保存，切换时原子替换当前 `auth.json`，并尽量在同一个 thread 上继续原会话，保留 rollout、工具结果、压缩检查点和加密推理项。

它同时提供：

- **CLI / Headless**：三种系统都可用的 `codex-relay` 命令。
- **macOS 原生桌面端**：SwiftUI 应用，包含概览、账号、会话、诊断、设置和菜单栏。
- **Windows / Linux 桌面端**：共享 Python 核心的 Tkinter 客户端。
- **四种界面语言**：简体中文、繁体中文、English、日本語。

> **验证状态**：macOS 已完成本机构建、自动化测试和主要界面验收；Windows/Linux 已提供源码、安装脚本、Tk 客户端、PyInstaller 入口和 CI 矩阵，但尚未在真实 Windows/Linux 主机上完成原生运行验收。请不要把 CI 配置或构建成功等同于目标平台已经验证。

## 核心能力

- 账号凭据按账号隔离保存，文件权限为 `0600`；切换前自动备份，失败自动回滚。
- 使用隔离的临时 `CODEX_HOME` 执行官方 `codex login`，不覆盖已有登录。
- 按稳定的 `chatgpt_account_id` 匹配账号，不依赖邮箱或显示名称。
- 支持恢复指定 thread，保留 rollout、工具结果和压缩检查点。
- 提供额度、Token、缓存命中率、推理项、压缩和进程诊断。
- 所有退出/重启 ChatGPT 的操作都需要显式确认，不会被后台静默执行。
- 不修改 ChatGPT 网页端的 Electron cookie/session；只管理 Codex 使用的 `auth.json`。

## 平台支持

| 环境 | 桌面客户端 | CLI | 验证状态 |
|---|---|---|---|
| macOS 14+ | SwiftUI（`desktop/`） | `codex-relay` | 本机实际构建、测试、主要界面验收 |
| Windows 10/11 | Tkinter（`portable/`） | `codex-relay` | 源码、安装和构建入口已提供；待目标机验收 |
| Linux | Tkinter（`portable/`） | `codex-relay` | 源码、Tk 依赖说明和构建入口已提供；待目标机验收 |
| 无图形环境 / SSH | 不启动 GUI | `codex-relay` | CLI 路径可用；不依赖 Tk |

macOS 原生版是首选桌面体验；Windows/Linux 的 Tk 客户端复用同一套账号、额度、会话和 CLI 逻辑。详见 [平台说明](docs/PLATFORMS.md)。

## 安装

### 从 Release 安装

Release 会提供：

- `codex-relay-<version>-py3-none-any.whl`：独立 CLI。
- macOS：`CodexRelay-<version>-macOS.app.zip`。
- Windows：`CodexRelay-<version>-Windows.exe`。
- Linux：`CodexRelay-<version>-Linux`（Tk 桌面）及 CLI wheel。

下载对应产物后：

```sh
# CLI（任意平台，需要 Python 3.9+）
python3 -m pip install codex-relay-*.whl
codex-relay setup
codex-relay doctor
```

macOS 解压 `.app.zip` 后拖入“应用程序”；Windows/Linux 直接运行对应可执行文件。**首次启动前请先确保已安装 Codex CLI；如果没有，应用会显示安装引导，也可以用 `codex-relay setup` 手动指定路径。**

### 从源码安装

```sh
git clone https://github.com/xiaodianshiuwu/codex-relay.git
cd codex-relay

# macOS / Linux
./install.sh

# Windows PowerShell
.\install.ps1

codex-relay setup
codex-relay doctor
```

安装脚本只复制程序文件到用户目录，不修改 Codex 的认证、配置或会话。

### macOS 原生桌面端

```sh
cd desktop
./build.sh
open "$HOME/Applications/Codex Relay.app"
```

需要 macOS 14+、Xcode Command Line Tools 和 Swift 5.9+。

### Windows / Linux Tk 桌面端

```sh
python3 -m pip install pyinstaller
./portable/build.sh          # Windows: .\portable\build.ps1
```

也可以直接运行源码：

```sh
codex-relay-gui
# 或
python3 -m codex_relay.portable_gui
```

Linux 需要系统 Tk：Debian/Ubuntu `sudo apt install python3-tk`，Fedora `sudo dnf install python3-tkinter`。

## 首次启动与 Codex 自动发现

应用和 CLI 使用同一套发现顺序：

1. `CODEX_RELAY_CODEX` 环境变量（显式路径优先）。
2. `~/.codex-relay/config.json` 中已经验证并保存的 `codex_binary`。
3. 系统 `PATH` 中的 `codex`。
4. 常见安装目录（macOS Homebrew、`~/.local/bin`、Windows `LOCALAPPDATA` 等）。

Codex 数据目录默认是 `~/.codex`，按以下顺序确定：

1. `CODEX_HOME` 环境变量。
2. `~/.codex-relay/config.json` 中的 `codex_home`。
3. `~/.codex`。

### 自动发现失败时

先安装 Codex CLI，然后运行：

```sh
codex-relay setup
```

如果 Codex 安装在非标准位置：

```sh
# macOS / Linux
codex-relay setup --codex /path/to/codex --codex-home /path/to/.codex

# Windows PowerShell
codex-relay setup --codex C:\path\to\codex.exe --codex-home C:\Users\<you>\.codex
```

`setup` 会验证 CLI 是否可执行、读取版本，并把路径写入 `~/.codex-relay/config.json`（`0600`）。之后 `codex-relay doctor`、GUI 和 CLI 都会使用这个配置。清除手动配置：

```sh
codex-relay setup --reset
```

> 不需要修改源代码，也不需要编辑复杂配置文件。环境变量只作为自动化/高级覆盖手段。

## 快速开始

```sh
# 1. 查看环境和诊断
codex-relay setup
codex-relay doctor

# 2. 把当前已登录账号加入仓库
codex-relay add --capture personal

# 3. 添加第二个账号（浏览器会打开官方登录）
codex-relay add work

# 4. 查看账号和额度
codex-relay list --refresh

# 5. 先看切换计划，不修改任何凭据或进程
codex-relay switch work --dry-run

# 6. 明确允许重启 ChatGPT 后执行切换
codex-relay switch work --allow-chatgpt-restart

# 7. 查看并恢复原会话
codex-relay sessions
codex-relay resume <thread-id> --account work --allow-chatgpt-restart
```

默认不会自动重启 ChatGPT。`switch --cli` / `switch --none` 可以在没有桌面 App 或不允许进程控制的环境中使用；自动化脚本必须显式传递 `--allow-chatgpt-restart`，并自行确保没有其他 Codex 进程共享同一 `CODEX_HOME`。

## CLI

```sh
codex-relay --help
codex-relay status
codex-relay doctor
codex-relay list
codex-relay sessions --limit 20
codex-relay inspect <thread-id>
codex-relay usage --cached
codex-relay setup
```

所有只读命令都支持 `--json`。`--lang zh-Hans|zh-Hant|en|ja` 可以覆盖界面语言。

## 数据与安全

- `~/.codex-relay/state.json`：账号元数据、活动账号、最近切换记录（`0600`）。
- `~/.codex-relay/profiles/<account_id>/auth.json`：各账号凭据（`0600`）。
- `~/.codex-relay/backups/`：切换前备份。
- `~/.codex-relay/journal.jsonl`：不包含 token 的切换审计。
- `~/.codex/auth.json`：Codex 当前活动凭据；Codex Relay 只按用户确认替换它。

请勿把上述目录、日志、截图或 `auth.json` 提交到 Git。应用不会把凭据写入日志；如果发现泄密，请立即撤销/轮换对应凭据，而不是只删除文件。

## 开发

```sh
python3 tests/run.py
python3 -m codex_relay.portable_gui --self-test
swift test --package-path desktop --scratch-path /tmp/codex-relay-swift
python3 tools/build_strings.py --check
python3 tools/i18n_audit.py --json
```

见 [开发说明](docs/DEVELOPMENT.md)、[贡献指南](CONTRIBUTING.md) 和 [安全策略](SECURITY.md)。

## 许可证

MIT License。详见 [LICENSE](LICENSE) 与 [第三方声明](THIRD_PARTY_NOTICES.md)。

Codex、ChatGPT、OpenAI 是其各自权利人的商标；本项目不是 OpenAI 官方产品。
