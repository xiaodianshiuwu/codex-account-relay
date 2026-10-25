# Codex Relay macOS GUI

macOS 14+ 原生 SwiftUI 应用，也是当前唯一公开支持的产品形态。Release 版会把 Codex Relay 后端作为 `Contents/Resources/codex-relay` 内置到 App 中；GUI 本身不保存、不打印 token，只解析后端的 JSON 输出。

## 功能

- 概览：当前账号、额度、运行状态、快捷操作、最近会话。
- 账号：账号列表与详情、额度、切换并恢复指定会话、显示名称和头像。
- 会话：搜索、按创建账号筛选、缓存/推理/压缩分析。
- 诊断：`doctor` 检查项、环境信息和桌面进程。
- 设置：额度显示、数字格式、主题和四种语言。
- 菜单栏：快速切换账号、刷新用量、打开主窗口。

## 构建

```sh
cd desktop
./build.sh
open "$HOME/Applications/Codex Relay.app"
```

`build.sh` 会 release 构建、组装 `Codex Relay.app`、ad-hoc 签名并安装到 `~/Applications`。发布构建会把 `dist-cli/codex-relay` 复制进 App；本地没有该文件时回退到 PATH 中的 `codex-relay`。需要 macOS 14+、Xcode Command Line Tools 和 Swift 5.9+。

## 自检

```sh
"$HOME/Applications/Codex Relay.app/Contents/MacOS/CodexRelay" --self-test
CODEX_RELAY_TEST_MODE=1 swift test --scratch-path /tmp/codex-relay-swift-test
```

`--self-test` 只调用只读 CLI 命令，不会刷新 token 或修改认证。

## 安全边界

- 切换走 CLI 的官方路径（替换 `auth.json` + 重启 App），不做进程内凭据注入。
- 所有切换/重启前都必须勾选确认。
- GUI 不直接发送 ChatGPT 深链接，启动/深链接子进程会剥离 relay 专用权限标记。
- 找不到 Codex CLI 时显示可操作的错误；先运行 `codex-relay setup` 或安装 Codex。
