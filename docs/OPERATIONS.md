# 使用与诊断

支持 macOS 14+ SwiftUI App。以下 CLI 命令供内部诊断，后端位于：

```sh
RELAY="/Applications/Codex Relay.app/Contents/Resources/codex-relay"
"$RELAY" --version
"$RELAY" setup --json
"$RELAY" doctor --json
"$RELAY" status --json
"$RELAY" sessions --limit 0 --json
```

若安装在用户应用程序目录，请对应调整路径。

## 路径

```sh
"$RELAY" setup --codex /path/to/codex --codex-home /path/to/.codex
"$RELAY" setup --reset
```

`CODEX_RELAY_CODEX` 优先于保存的 CLI 路径，其次使用 PATH 和常见安装目录。`CODEX_HOME` 优先于保存的数据路径。`CODEX_RELAY_HOME` 同时作用于 App 设置与后端。

## 聊天定位

“会话”和切换确认页显示本机完整聊天列表，创建账号只作元数据，不限制其他账号恢复聊天。会话 ID 必须是明确的 UUID；手动切号可选择不恢复聊天。额度守护通过官方列表接口分页读取本机及 SSH 聊天，搜索可同时匹配标题、路径和 ID。SSH 聊天需先在 Codex 中连接主机并打开。

自动切号切换的是本机认证，不会替换 SSH 主机上的账号；远程原聊天使用额度守护。

## 自动接力设置

`~/.codex-relay/auto.json` 的主要字段：

```json
{
  "enabled": false,
  "allow_chatgpt_restart": false,
  "remaining_threshold": 10,
  "interval": 60,
  "strategy": "best",
  "thread_id": "",
  "prompt": ""
}
```

间隔为有限正数，剩余阈值为 0–100；空提示词只切号。旧配置没有 `allow_chatgpt_restart` 时视为未许可。App 每轮执行前重新读取文件；修改一个字段不会重置其他配置。

```sh
# 只查看计划，不中断、不切号、不重启、不发送消息
"$RELAY" auto --once --thread <完整UUID> --prompt '继续原任务' --dry-run --json
```

自动接力只在明确许可后中断原轮次。原轮次已正常完成或由用户停止时不重启任务。并行活动任务、未知额度、未加载原聊天或协议变化会阻止操作。没有新轮次回执就不会显示“续跑成功”。

## 守护诊断

```sh
"$RELAY" guard doctor
"$RELAY" guard gui-snapshot
"$RELAY" guard check <完整UUID>
"$RELAY" guard status
"$RELAY" guard --host <Codex主机ID> check <完整UUID>
"$RELAY" guard --host <Codex主机ID> stop <完整UUID>
```

`check`、`doctor`、`gui-snapshot` 不会注册监控或发送提示词。开始监控在 App 页面中进行，也可通过内部 `guard background` 命令显式开始。

守护使用 `~/Library/Application Support/CodexDesktopGuard/`；每个聊天由进程锁保证只注册一次。独立后台进程复用同一个内置后端，不依赖源码路径或系统 Python。退出 Relay 不会停止它们。停止操作写入取消标记，已有被服务接受的请求无法撤回。

## 错误与回执

自动接力回执位于 `~/.codex-relay/continuations/<thread-id>.json`。`interrupt-intent` 或 `resume-intent` 说明操作是否被接收尚未确认；不要删除记录后盲目重试，先检查原聊天。守护记录中的 `uncertain` 同理。

切号失败会报告原始失败及回滚结果，备份保留供恢复。若新应用无法退出，不会在它仍写入认证的同时偷偷覆盖旧认证。账号写入操作串行，缓存列表读取不等待网络刷新。

当前账号有运行中的 Codex 时只通过用量 HTTP 读取额度，即使传入强制刷新也不会在另一个 app-server 中抢用旋转 refresh token。不要把 HTTP 临时错误当作需要重新登录；界面会保留错误原因。


## 运行状态误报与本次强制切换

v0.5.3 起，切换确认页区分实时执行、待核实和已排除的历史残留。点击“重新检测”可更新；普通切换执行前会再次检查。无需删除旧聊天或修改 Codex 的 SQLite 数据库。

已亲自确认所有聊天均无正在执行的任务时，可勾选“我确认没有正在进行的聊天，强制切换”。该确认不保存到自动配置，只作用于当前手动切换；仍会正常退出并重启桌面 App。自动接力不会使用此选项。

命令行诊断：`codex-relay activity --json`。手动覆盖活动检测：`codex-relay switch <alias> --desktop --confirm-idle --allow-chatgpt-restart`。`--confirm-idle` 与更广的历史 `--force` 含义不同，不会强制杀进程或绕过额度/写入进程校验。
