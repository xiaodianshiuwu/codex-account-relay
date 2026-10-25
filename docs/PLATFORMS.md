# 平台支持

## 唯一支持范围

| 能力 | 支持状态 |
|---|---|
| macOS 14+ SwiftUI App | 支持并发布 |
| App 内置 Codex Relay 后端 | 随 App 打包 |
| Codex CLI | 外部依赖，需用户安装并登录 |
| Windows / Linux 桌面端 | **不支持、不发布** |
| macOS 13 / Tk 回退 | **不支持、不发布** |
| 独立 CLI / wheel | 仅内部源码与开发用途，不单独发布 |

从 0.4.0 起，Codex Relay 只面向 **macOS 14+ 的 SwiftUI App**。发布资产只包含 `CodexRelay-<version>-macOS-SwiftUI.zip`，其中 `Codex Relay.app` 内含 Relay 后端，不要求用户另外安装 Python 包或 `codex-relay` 可执行文件。

## 验证状态

- macOS 14+：Python 测试、Swift 测试、SwiftUI 构建、App 自检、默认窗口下的会话页/账号页实机检查已完成。
- App 内置后端：发布构建验证了 `Contents/Resources/codex-relay` 存在且可执行。
- Windows/Linux：**不再属于支持范围，未做目标机验收**。
- macOS 13/Tk：**不再属于支持范围，未做目标机验收**。

## 保留的历史代码

`portable/`、`codex_relay/portable_gui.py`、Windows/Linux 安装脚本和独立 CLI wheel 构建仍保留在仓库中，目的是保留历史实现和内部后端代码，不代表继续提供产品支持。它们可能随代码重构而过时，不应作为下载、安装或验收入口。

## 未覆盖的发布事项

- Apple Developer ID 公证和正式签名。
- 自动更新。
- 多用户、企业分发和 App Store 分发。
