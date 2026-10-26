# Third-Party Notices

Codex Relay 的源代码采用 MIT License。

项目自身不包含第三方 Python 运行时依赖；运行时使用 Python 标准库和可选的系统 Tk。构建独立桌面产物时使用 PyInstaller。上述组件不随源代码仓库重新授权，其许可证由各自项目提供：

- Python：PSF License。
- Tcl/Tk：Tcl/Tk License。
- PyInstaller：GPL-2.0-or-later with a special exception permitting distribution of bundled applications；仅在构建时使用。
- macOS SwiftUI/AppKit：随 macOS/Xcode 提供，受 Apple 的 SDK 条款约束。

`desktop/Assets` 中的应用图标为本项目资产；未包含游戏、第三方图标库或品牌素材。

Codex、ChatGPT、OpenAI 是其各自权利人的商标。本项目不是 OpenAI 官方产品，也不包含 OpenAI 的源代码或凭据。

## Integrated desktop quota guard

`desktop_guard/` and `codex_relay/guard_cli.py` integrate the user's local CodexDesktopGuard 2.2.
The desktop IPC (`desktop.py`) and RPC (`rpc.py`) implementations derive from
[progressrdx/codex-auto-resume](https://github.com/progressrdx/codex-auto-resume),
revision `22c773cbc4c4b05cbc0bdd082d10b1c247a6bc9a`, Copyright (c) 2026 progressrdx, MIT.
`upstream_policy.py` retains its `latest_turn` / `quota_failure` helpers.
The full upstream license is preserved in `licenses/LICENSE.progressrdx` and shipped inside the app.
The original guard GUI and private runtime data are not redistributed.
