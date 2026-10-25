# Codex Relay

**一个账号切换后还能继续原来会话的 Codex 小工具。** 不用重新开 thread，不用手动搬上下文。

[English](#english) · [日本語](#日本語) · [繁體中文](#繁體中文)

## 3 步开始

1. **准备 Codex**：先安装并登录 [Codex CLI](https://github.com/openai/codex)。如果已经能运行 `codex`，这一步可以跳过。
2. **下载 Codex Relay**：到 [Releases](https://github.com/xiaodianshiuwu/codex-account-relay/releases/latest) 选择你的系统，下载后打开/运行即可。
3. **让它自己找 Codex**：打开桌面端，或在终端运行：

   ```sh
   codex-relay setup
   codex-relay doctor
   ```

   看到 `Doctor` 检查通过，就可以添加账号了。找不到 Codex 时，`setup` 会告诉你下一步，不会直接崩溃。

> **不需要**改源代码、手写配置文件或研究项目结构。默认自动发现；只有 Codex 装在非标准位置时，才需要手动指定一次。

## 它适合谁

- 有多个 ChatGPT/Codex 账号，想快速切换但不想丢当前 thread 的人。
- 希望在切换前自动备份、失败能回滚，不想手动改 `auth.json` 的人。
- 想在 macOS、Windows、Linux 或纯 SSH 环境里用同一套账号逻辑的人。

## 你能得到什么

- **原会话继续**：切换账号后恢复同一个 thread，rollout、工具结果、压缩检查点和加密 reasoning 项尽量保留。
- **安全切换**：账号隔离保存；切换前备份，失败回滚；退出/重启 ChatGPT 必须明确确认。
- **少折腾**：自动发现 Codex CLI 和数据目录；找不到时有清楚的手动设置入口。
- **自动续跑**：剩余额度低于阈值时切换账号，并向原 thread 发送可自定义的续跑提示词。
- **看得懂**：账号、额度、Token、缓存、推理、会话和进程诊断集中在一个界面/CLI。
- **四种语言**：简体中文、繁體中文、English、日本語。

## 平台与下载

| 你的系统 | 推荐方式 | 下载文件 |
|---|---|---|
| macOS 14+ | 原生 SwiftUI App | `CodexRelay-<version>-macOS-SwiftUI.zip` |
| Windows 10/11 | Tk 桌面端 | `CodexRelay-<version>-Windows-x86_64.exe` |
| Linux | Tk 桌面端 | `CodexRelay-<version>-Linux-x86_64` |
| 任意系统 / SSH | 独立 CLI | `codex_relay-<version>-py3-none-any.whl` |

> **验证边界**：macOS 已完成本机实际构建、测试和主要界面验收；Windows/Linux 已有 CI 构建与自动化测试，但原生桌面交互仍需目标机人工验收。CI 通过不等于所有平台都已完整验证。

## 安装

最新版本与下载：[GitHub Releases](https://github.com/xiaodianshiuwu/codex-account-relay/releases/latest)。

### 从 Release 安装

Release 提供：

- `codex_relay-<version>-py3-none-any.whl`：独立 CLI。
- macOS：`CodexRelay-<version>-macOS-SwiftUI.zip`。
- Windows：`CodexRelay-<version>-Windows-x86_64.exe`。
- Linux：`CodexRelay-<version>-Linux-x86_64`（Tk 桌面）及 CLI wheel。

下载对应产物后：

```sh
# CLI（任意平台，需要 Python 3.9+）
python3 -m pip install codex_relay-*.whl
codex-relay setup
codex-relay doctor
```

macOS 解压下载的 `.zip` 后，把 `Codex Relay.app` 拖入“应用程序”；Windows/Linux 直接运行对应可执行文件。**首次启动前请先确保已安装 Codex CLI；如果没有，应用会显示安装引导，也可以用 `codex-relay setup` 手动指定路径。**

### 从源码安装

```sh
git clone https://github.com/xiaodianshiuwu/codex-account-relay.git
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

## 自动续跑

当当前账号的剩余额度低于阈值时，自动切换到仍有额度的账号，并在原 thread 上继续执行。提示词和阈值都可以自定义：

```sh
# 先看计划，不切换、不重启、不发送提示词
codex-relay auto --once --remaining-threshold 10 --prompt "继续完成当前任务，不要重做已完成部分" --dry-run

# 确认后启动守护（会重启 ChatGPT，必须显式确认）
codex-relay auto --enable --remaining-threshold 10 --prompt "继续完成当前任务，不要重做已完成部分" --interval 60 --allow-chatgpt-restart --yes
```

- 首次启动守护加 `--enable`（或先在桌面设置页勾选“启用自动续跑”）；设置写入 `~/.codex-relay/auto.json`，之后守护会读取这个开关。
- `--remaining-threshold 10`：剩余额度低于 10% 时准备切换；默认值可在 GUI 设置页修改。
- `--prompt "..."`：切换成功后通过官方 `codex exec resume` 发到原 thread；留空表示只切号、不续跑。
- 默认提示词是“继续”；设置保存在 `~/.codex-relay/auto.json`，与 GUI 设置共用。
- 守护仍然需要显式 `--allow-chatgpt-restart --yes`，不会在后台静默重启 ChatGPT。

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


---

## English

**Start in three steps:** (1) install and sign in to the Codex CLI; (2) download the release for your OS; (3) open the app, or run `codex-relay setup` and `codex-relay doctor` in a terminal. If Codex is already on your `PATH`, Relay finds it automatically.

**Codex Relay** is a community-built, cross-platform handoff tool for Codex accounts. It stores each ChatGPT/Codex account in an isolated profile, atomically switches the active `auth.json`, and resumes the same thread whenever possible so rollout history, tool results, compaction checkpoints and encrypted reasoning items stay usable.

### What you get

- A Python CLI that works without a GUI.
- A native SwiftUI macOS app and a Tk desktop client for Windows/Linux.
- Four UI languages: Simplified Chinese, Traditional Chinese, English and Japanese.
- Quota, token, cache, reasoning and process diagnostics.
- Explicit confirmation before quitting or restarting ChatGPT; no silent background restarts.

### Platform status

| Platform | Desktop client | Status |
|---|---|---|
| macOS 14+ | SwiftUI | Built and smoke-tested on macOS |
| Windows 10/11 | Tk | Source, installer and CI build; native GUI acceptance still pending |
| Linux | Tk | Source and CI build; native GUI acceptance still pending |
| Headless/SSH | CLI only | Supported |

Do not treat a successful CI build as proof that every native desktop interaction has been verified.

### Install

Download the latest release from [GitHub Releases](https://github.com/xiaodianshiuwu/codex-account-relay/releases/latest), then choose the asset for your platform:

- macOS SwiftUI: unzip `CodexRelay-*-macOS-SwiftUI.zip` and move the app to Applications.
- Windows: run `CodexRelay-*-Windows-x86_64.exe`.
- Linux: run `CodexRelay-*-Linux-x86_64` (Tk and a display server are required).
- CLI on any platform: `python3 -m pip install codex_relay-*.whl`.

From source:

```sh
git clone https://github.com/xiaodianshiuwu/codex-account-relay.git
cd codex-relay
./install.sh          # Windows: .\install.ps1
codex-relay setup
codex-relay doctor
```

### First run and Codex discovery

Codex Relay looks for the Codex CLI in this order:

1. `CODEX_RELAY_CODEX`.
2. `~/.codex-relay/config.json` (`codex_binary`).
3. `PATH`.
4. Common installation directories.

The Codex data directory is selected from `CODEX_HOME`, then the saved `codex_home`, then `~/.codex`.

If automatic discovery fails, install Codex first. To pin a non-standard installation:

```sh
codex-relay setup --codex /path/to/codex --codex-home /path/to/.codex
codex-relay setup --json
```

The path is validated and saved with user-only permissions. Clear it with `codex-relay setup --reset`. Existing `auth.json`, configuration and sessions are never overwritten by setup.

### CLI quick start

```sh
codex-relay setup
codex-relay doctor
codex-relay add --capture personal
codex-relay add work
codex-relay list --refresh
codex-relay switch work --dry-run
codex-relay switch work --allow-chatgpt-restart
codex-relay sessions
codex-relay resume <thread-id> --account work --allow-chatgpt-restart
```

Read-only commands support `--json`. Use `--lang zh-Hans|zh-Hant|en|ja` to override the UI language.

### Auto-continue

When the remaining quota falls below a threshold, Relay can switch to another usable account and send a custom continuation prompt to the original thread:

```sh
codex-relay auto --once --remaining-threshold 10 --prompt "Continue the current task; do not redo completed work" --dry-run
codex-relay auto --enable --remaining-threshold 10 --prompt "Continue the current task; do not redo completed work" --interval 60 --allow-chatgpt-restart --yes
```

Settings live in `~/.codex-relay/auto.json` and are shared with the desktop settings page. The daemon still requires explicit `--allow-chatgpt-restart --yes`.

### Security

Never commit `auth.json`, tokens, cookies, account emails, thread IDs or full session logs. If a credential is exposed, revoke/rotate it; deleting a file is not enough. See [SECURITY.md](SECURITY.md), [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Codex, ChatGPT and OpenAI are trademarks of their respective owners. This is not an official OpenAI product.

---

## 日本語

**3 ステップで開始：**（1）Codex CLI をインストールしてログイン、（2）お使いの OS 用の Release をダウンロード、（3）アプリを開くか、ターミナルで `codex-relay setup` と `codex-relay doctor` を実行。Codex が `PATH` にあれば自動検出されます。

**Codex Relay** は、Codex の複数アカウントを切り替えるためのコミュニティ製クロスプラットフォームツールです。アカウントごとに認証情報を分離して保存し、`auth.json` をアトミックに切り替えたうえで、できるだけ同じ thread を再開します。rollout、ツール結果、圧縮チェックポイント、暗号化された reasoning item を維持できます。

### 主な機能

- GUI を必要としない Python CLI。
- macOS のネイティブ SwiftUI アプリと、Windows/Linux 向け Tk デスクトップクライアント。
- 簡体字中国語、繁体字中国語、英語、日本語の 4 言語 UI。
- クォータ、Token、キャッシュ、reasoning、プロセスの診断。
- ChatGPT の終了・再起動前には必ず明示確認。バックグラウンドで勝手に再起動しません。

### 対応状況

| 環境 | デスクトップ | 状態 |
|---|---|---|
| macOS 14+ | SwiftUI | macOS でビルドとスモークテスト済み |
| Windows 10/11 | Tk | ソース、インストーラー、CI ビルドあり。ネイティブ GUI の受け入れ確認は未完了 |
| Linux | Tk | ソースと CI ビルドあり。ネイティブ GUI の受け入れ確認は未完了 |
| ヘッドレス / SSH | CLI のみ | 対応 |

CI のビルド成功は、すべてのネイティブ GUI 動作を検証済みという意味ではありません。

### インストール

[GitHub Releases](https://github.com/xiaodianshiuwu/codex-account-relay/releases/latest) から環境に合うファイルをダウンロードします。

- macOS SwiftUI: `CodexRelay-*-macOS-SwiftUI.zip` を展開して Applications に移動。
- Windows: `CodexRelay-*-Windows-x86_64.exe` を実行。
- Linux: `CodexRelay-*-Linux-x86_64` を実行（Tk と表示環境が必要）。
- CLI: `python3 -m pip install codex_relay-*.whl`。

ソースからインストールする場合:

```sh
git clone https://github.com/xiaodianshiuwu/codex-account-relay.git
cd codex-relay
./install.sh          # Windows: .\install.ps1
codex-relay setup
codex-relay doctor
```

### 初回セットアップと Codex の自動検出

検出順序は次のとおりです。

1. `CODEX_RELAY_CODEX` 環境変数。
2. `~/.codex-relay/config.json` の `codex_binary`。
3. `PATH` 上の `codex`。
4. 一般的なインストール先。

データディレクトリは `CODEX_HOME`、保存済みの `codex_home`、`~/.codex` の順で決定します。

自動検出できない場合は、先に Codex CLI をインストールしてください。標準外の場所にある場合は次のように指定できます。

```sh
codex-relay setup --codex /path/to/codex --codex-home /path/to/.codex
codex-relay setup --json
```

パスは検証後にユーザー専用権限で保存されます。設定を消すには `codex-relay setup --reset` を実行します。既存の `auth.json`、設定、セッションは setup では上書きされません。

### CLI クイックスタート

```sh
codex-relay setup
codex-relay doctor
codex-relay add --capture personal
codex-relay add work
codex-relay list --refresh
codex-relay switch work --dry-run
codex-relay switch work --allow-chatgpt-restart
codex-relay sessions
codex-relay resume <thread-id> --account work --allow-chatgpt-restart
```

読み取り専用コマンドは `--json` に対応しています。UI 言語は `--lang zh-Hans|zh-Hant|en|ja` で切り替えられます。

### 自動継続

残り枠がしきい値を下回ったら、別の使用可能なアカウントへ切り替え、元の thread にカスタムの継続プロンプトを送れます：

```sh
codex-relay auto --once --remaining-threshold 10 --prompt "現在のタスクを続行し、完了済みの作業はやり直さないでください" --dry-run
codex-relay auto --enable --remaining-threshold 10 --prompt "現在のタスクを続行し、完了済みの作業はやり直さないでください" --interval 60 --allow-chatgpt-restart --yes
```

設定は `~/.codex-relay/auto.json` に保存され、デスクトップの設定ページと共有されます。デーモンには引き続き明示的な `--allow-chatgpt-restart --yes` が必要です。

### セキュリティ

`auth.json`、Token、Cookie、アカウントのメールアドレス、thread ID、完全なセッションログを Git にコミットしないでください。漏えいが疑われる場合は、ファイルを削除するだけでなく、必ず認証情報を失効・ローテーションしてください。詳細は [SECURITY.md](SECURITY.md)、[LICENSE](LICENSE)、[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) を参照してください。

Codex、ChatGPT、OpenAI は各権利者の商標です。本プロジェクトは OpenAI 公式製品ではありません。

---

## 繁體中文

**3 個步驟開始：**（1）安裝並登入 Codex CLI；（2）下載符合你系統的 Release；（3）開啟 App，或在終端機執行 `codex-relay setup` 與 `codex-relay doctor`。如果 Codex 已在 `PATH`，Relay 會自動找到它。

**Codex Relay** 是社群開發的 Codex 多帳號接力工具。它將每個 ChatGPT/Codex 帳號的認證資料隔離保存，以原子方式切換目前的 `auth.json`，並盡可能在同一條 thread 上繼續原會話，保留 rollout、工具結果、壓縮檢查點與加密 reasoning 項目。

### 主要功能

- 不需要圖形介面的 Python CLI。
- macOS 原生 SwiftUI 應用程式，以及 Windows/Linux 的 Tk 桌面用戶端。
- 四種介面語言：簡體中文、繁體中文、English、日本語。
- 額度、Token、快取、reasoning 與程序診斷。
- 結束或重新啟動 ChatGPT 前一定要求明確確認，不會在背景自行重啟。

### 平台狀態

| 環境 | 桌面用戶端 | 狀態 |
|---|---|---|
| macOS 14+ | SwiftUI | 已在 macOS 建置與煙霧測試 |
| Windows 10/11 | Tk | 已提供原始碼、安裝程式與 CI 建置；原生 GUI 驗收尚未完成 |
| Linux | Tk | 已提供原始碼與 CI 建置；原生 GUI 驗收尚未完成 |
| 無圖形環境 / SSH | 僅 CLI | 支援 |

CI 建置成功不等於所有原生 GUI 互動都已經驗證。

### 安裝

從 [GitHub Releases](https://github.com/xiaodianshiuwu/codex-account-relay/releases/latest) 下載適合的檔案：

- macOS SwiftUI：解壓縮 `CodexRelay-*-macOS-SwiftUI.zip`，將 App 拖到「應用程式」。
- Windows：執行 `CodexRelay-*-Windows-x86_64.exe`。
- Linux：執行 `CodexRelay-*-Linux-x86_64`（需要 Tk 與顯示環境）。
- CLI：`python3 -m pip install codex_relay-*.whl`。

從原始碼安裝：

```sh
git clone https://github.com/xiaodianshiuwu/codex-account-relay.git
cd codex-relay
./install.sh          # Windows: .\install.ps1
codex-relay setup
codex-relay doctor
```

### 首次啟動與 Codex 自動偵測

偵測順序如下：

1. `CODEX_RELAY_CODEX` 環境變數。
2. `~/.codex-relay/config.json` 的 `codex_binary`。
3. `PATH` 中的 `codex`。
4. 常見安裝目錄。

資料目錄依序使用 `CODEX_HOME`、已保存的 `codex_home`、`~/.codex`。

若無法自動偵測，請先安裝 Codex CLI。若 Codex 安裝在非標準位置：

```sh
codex-relay setup --codex /path/to/codex --codex-home /path/to/.codex
codex-relay setup --json
```

路徑會經驗證後以使用者專用權限保存；執行 `codex-relay setup --reset` 可清除。setup 不會覆寫既有 `auth.json`、設定或會話。

### CLI 快速開始

```sh
codex-relay setup
codex-relay doctor
codex-relay add --capture personal
codex-relay add work
codex-relay list --refresh
codex-relay switch work --dry-run
codex-relay switch work --allow-chatgpt-restart
codex-relay sessions
codex-relay resume <thread-id> --account work --allow-chatgpt-restart
```

唯讀命令支援 `--json`。可用 `--lang zh-Hans|zh-Hant|en|ja` 覆寫介面語言。

### 自動續跑

當目前帳號的剩餘額度低於閾值時，可以切換到仍有額度的帳號，並將自訂續跑提示詞送到原 thread：

```sh
codex-relay auto --once --remaining-threshold 10 --prompt "繼續目前任務，不要重做已完成的部分" --dry-run
codex-relay auto --enable --remaining-threshold 10 --prompt "繼續目前任務，不要重做已完成的部分" --interval 60 --allow-chatgpt-restart --yes
```

設定保存在 `~/.codex-relay/auto.json`，與桌面設定頁共用。守護仍需要明確加上 `--allow-chatgpt-restart --yes`。

### 安全性

請勿將 `auth.json`、Token、Cookie、帳號電子郵件、thread ID 或完整會話日誌提交到 Git。若憑證可能外洩，請立即撤銷/輪換，而不是只刪除檔案。詳見 [SECURITY.md](SECURITY.md)、[LICENSE](LICENSE) 與 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

Codex、ChatGPT、OpenAI 為其各自權利人的商標；本專案不是 OpenAI 官方產品。

---

## 许可证

MIT License。详见 [LICENSE](LICENSE) 与 [第三方声明](THIRD_PARTY_NOTICES.md)。

Codex、ChatGPT、OpenAI 是其各自权利人的商标；本项目不是 OpenAI 官方产品。
