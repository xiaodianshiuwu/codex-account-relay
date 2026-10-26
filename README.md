# Codex Relay

macOS 上的 Codex 多账号管理与原聊天额度守护工具。原生 SwiftUI 界面，内置后端；支持简体中文、繁體中文、English、日本語。

[下载最新版本](https://github.com/xiaodianshiuwu/codex-account-relay/releases/latest) · [使用说明](docs/OPERATIONS.md) · [开发与验证](docs/DEVELOPMENT.md) · [English](#english)

## 两种工作方式

| 目标 | 使用入口 | 行为 |
|---|---|---|
| 管理多个账号、手动切换 | 账号 | 校验目标账号，备份凭据，切换账号；可明确选择需要恢复的本机聊天 |
| 低额度时换账号继续任务 | 设置 → 自动接力 | 明确指定本机聊天，确认中断原轮次后切号，重新打开原聊天，通过桌面端接口启动下一轮 |
| 保持原账号，等额度恢复后继续 | 额度守护 | 支持本机及 SSH 原聊天；低额度中断，恢复后继续，不切号、不重启 ChatGPT |

**自动接力切号需要重启 ChatGPT。** 不希望重启时，使用“额度守护”。两种自动化不能同时接管本机聊天；切号前需停止本机守护。

## 安装

支持 **macOS 14+ SwiftUI App**。Apple Silicon 是本次验证平台；其他架构没有实机验收。

1. 安装并登录 [Codex CLI](https://github.com/openai/codex)，并安装 Codex 桌面应用。
2. 从 [Releases](https://github.com/xiaodianshiuwu/codex-account-relay/releases/latest) 下载 `CodexRelay-v0.5.1-macOS-SwiftUI.zip`。
3. 解压，将 `Codex Relay.app` 拖到应用程序目录后打开。Release 的 `SHA256SUMS.txt` 可用于核对下载内容。

App 已包含 Relay 后端和 Python 运行时，无需另外安装 Python 或 Relay CLI。当前使用 ad-hoc 签名，尚无 Apple Developer ID 签名及公证。

**从 0.4.x 升级：** 账号及自动配置保留。旧配置中的 `enabled=true` 不再自动取得重启许可；请检查设置页的独立重启开关，并明确选择续跑聊天。

## 添加账号与手动切换

在“账号”中捕获当前登录账号，或添加其他账号完成隔离登录。切号确认页列出目标账号、额度和恢复聊天，可选择“仅切换账号”。聊天不按创建账号过滤，不再只显示前 12 条，也不会隐式猜测最近聊天。

手动切换会备份 `auth.json` 和 Relay 状态，退出桌面应用后写入目标凭据，再启动并打开选定聊天。失败时尝试恢复备份。**打开聊天与发送续跑文字是两个不同结果**；确认页可勾选“切号后继续原聊天”并填写提示词；不勾选时只恢复聊天，勾选时与自动接力共用桌面原聊天续跑链路。

## 自动接力

在“设置”中配置：

- 开启自动续跑，并单独允许自动接力重启 ChatGPT。
- 选择**明确的本机聊天**和非空续跑提示词；空提示词表示只切号。
- 设置剩余额度阈值、检查间隔、按账号顺序或最佳额度选择候选。

执行时重新读取账号额度；缺失、非有限或过期的数据不当作可用额度。原聊天运行中时，先取得指定轮次的中断确认；其他活动聊天会阻止切号。切号后等待原聊天加载，核验原轮次、模型、目录及权限，通过桌面端原聊天接口发送提示词。收到新轮次 ID 后才显示续跑成功。

结果不确定时保留操作记录且不自动重发。已完成、由用户停止、等待审批或输入的聊天不会由自动接力重新开始。手动确认页明确选择续跑时，可继续用户已中断的聊天。没有可用候选或无法确认原聊天时显示原因。自动接力只在 Relay 运行期间执行，退出后停止；尚未开始的检查会尊重最新的关闭设置。

## 原聊天额度守护

在“额度守护”查看本机与 Codex 已配置的 SSH 主机。先在 Codex 中打开目标聊天，再选择“开始守护”。

- 默认小时剩余 **≤5%** 时中断，恢复到 **≥10%** 后继续；两项阈值均可调整。
- 周额度只显示，不触发本地百分比阈值；服务明确拒绝额度时仍会等待。
- 可调整检查间隔及续跑文字。间隔接受有限正数，提示词无任意长度上限，默认不限制续跑次数。
- 继续使用原聊天、模型、工作目录和权限。不响应审批，不转移到另一个 CLI 任务。
- 后续窗口根据接口重置时间和窗口长度估算，不是可用额度承诺。
- **开始后独立后台运行，退出 Relay 后仍会监控。** 使用“停止守护”取消后续操作；不会结束当前运行中的任务。Mac 睡眠期间无法检查。
- 修改设置影响下次开始的监控；已有进程继续使用启动时的配置。发现协议不兼容或操作回执未知时，显示原因并停止发送。

本项目集成本机 CodexDesktopGuard 2.2 后端，并保留第三方 MIT 声明。已有守护记录继续从 `~/Library/Application Support/CodexDesktopGuard/` 读取，不删除历史记录。

## 数据位置与路径设置

| 内容 | 默认位置 |
|---|---|
| Relay 账号、配置、备份 | `~/.codex-relay/` |
| 自动接力设置 | `~/.codex-relay/auto.json` |
| 自动接力回执 | `~/.codex-relay/continuations/` |
| Codex 数据及当前认证 | `~/.codex/` |
| 原聊天守护设置、状态和日志 | `~/Library/Application Support/CodexDesktopGuard/` |

自动发现失败可用内置后端的 `setup` 命令保存路径；详见[使用说明](docs/OPERATIONS.md)。`CODEX_RELAY_HOME`、`CODEX_HOME`、`CODEX_RELAY_CODEX`、`CODEX_RELAY_GUARD_HOME` 可覆盖默认位置。设置了 `CODEX_RELAY_HOME` 且未单独指定守护目录时，守护记录写入其 `guard/` 子目录，便于隔离测试。

账号凭据保存在各 profile 的本机文件中，**没有额外加密**；文件权限为当前用户可读写。不要把凭据、运行数据或真实聊天截图上传到仓库。备份默认全部保留；需要自动清理时，可在 `state.json` 的 `settings.backup_keep` 设置保留数量，`0` 表示全部保留。

## 源码构建与测试

```sh
git clone https://github.com/xiaodianshiuwu/codex-account-relay.git
cd codex-account-relay
python3 -m venv .venv
.venv/bin/python -m pip install pyinstaller
.venv/bin/python tools/build_cli_bundle.py
./desktop/build.sh
```

结果位于 `desktop/build/Codex Relay.app`。构建不会结束已有应用或自动启动新应用；需要复制到其他位置时使用 `CODEX_RELAY_GUI_APP=/new/path/Codex\ Relay.app`。需要 Swift 6 工具链（Swift 5 语言模式）。

```sh
python3 tests/run.py
swift test --package-path desktop
python3 tools/build_strings.py --check
python3 tools/i18n_audit.py --json
```

CLI 是 App 的内部后端，不单独发布。Windows、Linux 和 Tk 源码作为历史实现保留，不属于支持及发布范围。

## 验证边界

v0.5.1 对账号写入/回滚、并发写入、原聊天中断与续跑回执、本机/SSH 状态机、设置和原生桥接执行了隔离测试。当前桌面版本 `26.1021.31345` 的本机/SSH 聊天定位与额度读取已做真实只读验证。

按用户要求，本次没有重启 ChatGPT，没有在真实账号间切换，也没有向业务聊天发送续跑文字。因此，**隔离测试通过不等于真实切号续跑已验收**。桌面 IPC 不是稳定公共 API；升级后按实际协议响应检查，不使用精确版本白名单，也不保证未来版本自动兼容。详情见[审查报告](docs/AUDIT-2026-10-26.md)。

保留原聊天可以继续使用其持久历史；不承诺冻结服务端推理或保证缓存命中。

## English

Codex Relay combines account management and original-chat quota monitoring in a native macOS 14+ SwiftUI app. The release includes its Python backend. Install the Codex CLI and desktop app separately.

- **Account relay:** select an exact local chat, explicitly permit ChatGPT restarts, check candidate quota, interrupt the original turn with a confirmed receipt, switch credentials, reopen that chat, and continue through the desktop protocol. Success requires a new turn ID. An uncertain request is never resent.
- **Quota guard:** monitor an explicitly selected local or SSH chat without switching accounts or restarting ChatGPT. Pause at a configurable hourly quota threshold, wait for recovery, then continue with the original execution settings. Weekly quota is display only. Background monitors continue after Relay quits; stop them from the guard page.
- **Chat discovery:** no account-origin filtering, first-12 truncation, or first-100 cut-off. Switching and continuation never silently select a recent chat.
- **Upgrade:** legacy `enabled=true` does not grant restart permission. Review the separate permission and exact target chat.
- Four UI languages, shared persistent settings, atomic credentials and state, retained backups, durable operation receipts. Credential files are private local files, not an encrypted vault.

Download the macOS SwiftUI archive and checksum file from [Releases](https://github.com/xiaodianshiuwu/codex-account-relay/releases/latest). Apple Silicon is the verified build target; the app is ad-hoc signed and not notarized. Windows/Linux/Tk sources are historical and unsupported.

Validation includes isolated switch/rollback and original-chat protocol tests, plus live read-only local/SSH discovery. **No real account switch, ChatGPT restart, or business-chat continuation was performed in this audit.** Desktop IPC compatibility is checked at runtime and may change with app updates.

## 日本語

macOS 14+ 向けのアカウント管理・元のチャットの利用枠監視ツールです。自動アカウント切り替えには ChatGPT の再起動許可と明示的なローカルチャットの選択が必要です。再起動しない場合は「利用枠ガード」で本機または SSH の元チャットを監視できます。監視は Relay 終了後も続くため、画面から停止してください。

本監査では実アカウントの切り替え、ChatGPT の再起動、業務チャットへの送信は行っていません。分離テストと実環境の読み取り確認を区別しています。

## 繁體中文

macOS 14+ 原生帳號管理與原聊天額度守護工具。自動切換帳號需要獨立的 ChatGPT 重啟許可，並明確選擇本機聊天；不希望重啟時，使用「額度守護」監控本機或 SSH 原聊天。守護啟動後會在背景持續執行，退出 Relay 不會停止，請在介面中停止守護。

本次沒有執行真實切號、重啟 ChatGPT 或向業務聊天傳送續跑文字；隔離測試與真實唯讀驗證分別記錄。

## License

[MIT](LICENSE) · [第三方声明](THIRD_PARTY_NOTICES.md)。Codex、ChatGPT 和 OpenAI 商标属于其各自权利人；本项目不是 OpenAI 官方产品。

若源码位于 iCloud/File Provider 同步目录，系统可能自动给 App 添加 Finder 元数据，导致严格签名校验失败。可用 `CODEX_RELAY_BUILD_DIR=/tmp/codex-relay-build ./desktop/build.sh` 将组装与签名放在同步目录之外；不要跳过签名校验。
