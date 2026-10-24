# 日常使用与故障处理

## 安装与升级

### macOS

```sh
cd codex-relay
./install.sh
codex-relay doctor

# 原生 SwiftUI（推荐）
cd desktop && ./build.sh
open "$HOME/Applications/Codex Relay.app"
```

### Windows

```powershell
cd <仓库目录>\codex-relay
.\install.cmd
%USERPROFILE%\.local\bin\codex-relay.cmd doctor
%USERPROFILE%\.local\bin\codex-relay-gui.cmd
```

安装到 `%USERPROFILE%\.local\share\codex-relay`，启动器在 `%USERPROFILE%\.local\bin`。如果 PowerShell 执行策略阻止脚本，使用 `powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1`。

### Linux

```sh
cd codex-relay
sudo apt install python3-tk   # Debian/Ubuntu；Fedora 用 python3-tkinter
./install.sh
codex-relay doctor
codex-relay-gui
```

无桌面/SSH 时不要启动 GUI，直接使用 CLI。Tk 客户端在无 `$DISPLAY` 时会提示并退出，不会假装成功。

升级 Codex 后：

```sh
codex-relay doctor
codex-relay switch <别名> --thread none
```

如果 `doctor` 报数据库或深链接问题，先不要在活动轮次中重启；确认没有正在执行的任务后再切换。

## 首次配置

```sh
# 把当前账号纳入仓库
codex-relay add --capture personal

# 添加第二个账号：会打开浏览器
codex-relay add work
```

添加第二个账号时：

- 不要在已登录账号的普通浏览器窗口里直接确认；请用无痕窗口或切换浏览器账号。
- 如果浏览器登录不可用，尝试 `codex-relay add work --device`（需要账号开启 device code 登录）。
- 工具使用隔离的临时 `CODEX_HOME`，不会撤销当前账号。

查看：

```sh
codex-relay list
codex-relay list --refresh
codex-relay usage --all
```

## 桌面 App 切换

```sh
codex-relay switch work --dry-run
codex-relay switch work --allow-chatgpt-restart
```

流程：

0. 先 `--dry-run` 查看计划；实际执行必须显式 `--allow-chatgpt-restart`（GUI 必须勾选确认）。
1. 检查目标账号凭据，并通过隔离 app-server 校验额度（可 `--no-verify` 跳过）。
2. 检查是否有正在执行的轮次；默认拒绝中途重启。
3. 退出 ChatGPT App，等待主进程、app-server、exec-server 退出。
4. 备份当前 auth.json 与 state。
5. 把活动账号最新 token 写回仓库。
6. 原子写入目标 auth.json。
7. 启动 ChatGPT App，等待新的 app-server。
8. 打开 `codex://threads/<最近会话>`。
9. 失败时恢复备份并重启 App。

常用参数：

```sh
codex-relay switch work --thread none --allow-chatgpt-restart     # 不自动打开会话
codex-relay switch work --thread <id> --allow-chatgpt-restart     # 指定会话
codex-relay switch work --no-verify --allow-chatgpt-restart       # 跳过网络校验（离线/接口异常时）
codex-relay switch work --force --allow-chatgpt-restart           # 中断活动轮次或存在其他 Codex 进程时
```

## CLI 切换与恢复

CLI 不使用共享 daemon，避免 daemon 缓存旧账号：

```sh
codex-relay switch work --cli
codex-relay resume <thread-id> --account work --cli
```

`resume --cli` 实际执行：

```sh
codex --no-daemon resume <thread-id>
```

如果你在终端里直接运行 `codex` 而没有用 `--no-daemon`，它可能连接共享 daemon；切换后建议显式使用 `--no-daemon`，或先 `codex app-server daemon stop`。

## 查看与恢复会话

```sh
codex-relay sessions --limit 20
codex-relay sessions --cwd "$PWD"
codex-relay sessions --search "关键词"
codex-relay resume --last --desktop --allow-chatgpt-restart
codex-relay resume --last --account work --desktop --allow-chatgpt-restart
```

会话列表里的“创建账号”是 `creator_account_id`，不会因为后续切换而改写。恢复时看的是 thread id，不是创建者。

## 用量与自动接力

```sh
codex-relay usage
codex-relay usage --all
codex-relay usage --all --json
codex-relay auto --once --dry-run                    # 只给建议，不执行
codex-relay auto --once --threshold 95 --allow-chatgpt-restart --yes  # 明确允许自动重启
```

- 活动账号使用只读 HTTP 用量接口，不启动第二个 app-server，避免 refresh token 竞态。
- 非活动账号通过隔离官方 app-server 刷新；刷新后的 token 会立即写回。
- `auto` 默认只建议；不加 `--allow-chatgpt-restart --yes` 不会重启 ChatGPT。
- 检测到活动轮次时阻止切换；确认可中断时加 `--force`。
- 退出码：0 已切换/演练成功，2 无需切换，3 被活动轮次或进程控制策略阻止，1 错误。

常驻守护：

```sh
codex-relay auto --threshold 95 --interval 60
```

## 跨平台桌面 GUI（Windows / Linux / macOS fallback）

```sh
python -m codex_relay.portable_gui --self-test
python -m codex_relay.portable_gui
# 安装后：codex-relay-gui
```

- 概览：当前账号、5h/1w 额度、最近会话。
- 账号：列表、详情、切换确认、重命名、添加、头像。
- 会话：列表 + `inspect` 缓存/Token 分析。
- 诊断：`doctor --json` 检查项。
- 设置：剩余/已用、完整/K/M/B、跟随系统/暗色/亮色、简体中文/繁體中文/English/日本語。
- 设置文件：`~/.codex-relay/portable-ui.json`；账号、额度、会话和头像仍共享。

切换确认框只有在检测到 ChatGPT 桌面 App 时才使用 `--desktop --allow-chatgpt-restart`；否则使用 `--none`，只替换 `auth.json`，不会启动或退出任何进程。没有显示服务时使用 CLI。

## macOS 原生桌面 GUI（主窗口 + 菜单栏）

构建后启动：

```sh
cd desktop
./build.sh
open "$HOME/Applications/Codex Relay.app"
```

主窗口六个区：

- **概览**：当前账号、5h/1w 额度大数字、运行状态、快捷操作、最近 6 条会话
- **账号**：左侧列表（含头像和额度速览），右侧详情（额度条、账号信息、真实名称/自定义名称、头像上传/恢复、选择一条原会话后“切换并恢复”）
- **会话**：搜索 + 按创建账号筛选；详情自动读取缓存/推理/压缩统计，提供加载、无数据、失败重试状态，并支持完整数值或 K/M/B 简写
- **诊断**：`doctor` 全部检查项 + 环境 + 桌面进程
- **设置**：额度剩余/已用、数字完整/简写、主题跟随系统/暗色/亮色、语言简体中文/繁體中文/English/日本語；配置会持久化并即时生效
- **关于**：版本、安全边界、文档入口

添加账号在 GUI 内完成：输入别名 → 开始登录 → 浏览器选账号 → 输出实时显示 → 可取消。全程走隔离 `CODEX_HOME`，不撤销当前账号。

右上角菜单栏（`CX 别名`）保留：一键切换、刷新用量、打开主窗口、打开最近会话、退出。所有打开 ChatGPT/深链接入口都先弹确认框，并走 CLI `resume --allow-chatgpt-restart`。主窗口和菜单栏每 30 秒只读刷新（`list`/`status`/`sessions`，不刷新 token）；活动账号额度另每 10 秒通过只读 HTTP 同步，启动时和每 2 分钟全量刷新，打开非活动账号时会按需刷新。

切换中会弹出不可关闭的进度窗，实时显示 CLI 的阶段事件；失败自动回滚并显示错误，不会自动重试。

## Headless CLI（SSH / 无桌面）

```sh
# 不导入 Tk，不需要桌面 App
python -m codex_relay status --json
python -m codex_relay list --json
python -m codex_relay sessions --limit 20 --json
python -m codex_relay inspect <thread-id> --json
python -m codex_relay doctor --json

# 无桌面 App 时，只替换凭据；先确保没有其他 Codex 进程
python -m codex_relay switch work --none

# 或在没有其他进程时用 CLI 模式
python -m codex_relay switch work --cli

# 恢复会话时使用 CLI resume（无 GUI 深链接）
python -m codex_relay resume <thread-id> --account work --cli
```

非交互脚本请使用 `--json`；删除/覆盖操作需要 `--yes`；桌面重启/深链接仍必须显式 `--allow-chatgpt-restart`。没有 TTY 时交互确认会被拒绝，命令以错误码退出。

## 诊断与报告

```sh
codex-relay status
codex-relay doctor
codex-relay logs --limit 20
codex-relay inspect <thread-id>
codex-relay report <thread-id> --since 2026-10-22T23:32:00Z
```

`inspect` 输出：

- reasoning 项数量、其中带 encrypted_content 的数量和总字节数
- compaction 窗口、replacement history 类型、是否保留加密推理
- 每轮请求数、首/末输入 token、缓存命中率
- 切换边界对比（`report`）

## 故障处理

### `codex login` 提示 refresh token 已被使用

说明同一账号的 refresh token 被多个进程/机器并发刷新。解决：

```sh
codex-relay add <别名>          # 重新登录该账号
codex-relay switch <别名> --no-verify
```

不要让两个正在运行的 Codex 进程共享同一 CODEX_HOME；本工具会默认拒绝。

### 切换后 App 显示旧账号

1. 确认 `~/.codex/auth.json` 的 account_id 是目标账号：
   ```sh
   codex-relay status
   ```
2. 确认 ChatGPT App 已完全退出并重启（不是只关闭窗口）。
3. 如果仍有旧 app-server 进程：
   ```sh
   ps -axo pid,command | grep 'CodexCLI.app/Contents/MacOS/codex app-server'
   ```
   本工具会等待它们退出；如残留，使用 `switch --force` 或手动退出 App。
4. 如果使用了共享 daemon：
   ```sh
   codex app-server daemon stop
   ```

### 切换失败并回滚

`codex-relay logs` 查看失败事件；`~/.codex-relay/backups/` 下有切换前 `auth.json` 和 `state.json`。工具会尝试自动恢复并重启 App。若自动回滚也失败：

```sh
cp ~/.codex-relay/backups/<时间>-switch/auth.json ~/.codex/auth.json
chmod 600 ~/.codex/auth.json
open -b com.openai.codex
```

### 活动轮次导致切换被拒绝

这是保护行为。等待该轮结束，或确认已完成的工具结果已持久化后使用 `--force`。强制中断不会删除 rollout；之后仍可用原 thread id 恢复，但正在执行的工具调用可能没有输出。

### 报告没有切换前后数据

`report` 需要边界时间。使用：

```sh
codex-relay report <thread-id> --since <ISO时间>
```

`--since` 应设在切换后、下一次用户轮次之前。

## 安全

- 不要执行 `codex logout` 来“切换账号”；它会撤销当前会话。
- 不要在活动 `auth.json` 上直接登录另一个账号；官方登录会 best-effort 撤销旧会话。
- 不要提交 `~/.codex-relay/`、`auth.json`、备份目录。
- `export` 生成的文件包含 refresh token，等同于密码。
