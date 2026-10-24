# 平台支持与验证边界

本文区分 **代码已实现**、**在本机实际执行过** 和 **尚未执行**，不要把 CI 配置、源码存在或构建成功等同于目标系统已验证。

## 支持矩阵

| 能力 | macOS SwiftUI | Windows Tk | Linux Tk | Headless CLI |
|---|---|---|---|---|
| 账号仓库、添加、删除、重命名 | 是 | 是（共享核心） | 是（共享核心） | 是 |
| 切换账号（`auth.json`） | 是 | 是 | 是 | 是 |
| 重启/退出 ChatGPT 桌面 App | 是（AppleScript + 深链接） | 检测到官方 App 时支持 | 通常没有官方 App，安全回退为仅替换凭据 | 显式模式 |
| 深链接恢复原会话 | 是 | 检测到 App 时支持 | 无官方 App 时不可用 | `resume --cli` |
| 会话列表、rollout/inspect | 是 | 是 | 是 | 是 |
| 额度查询、Token 刷新 | 是 | 是 | 是 | 是 |
| 四种语言、主题、数字格式 | 是 | 是（HUD 风格近似） | 是（HUD 风格近似） | 不适用 |
| 无显示服务运行 | CLI 可用 | GUI 提示改用 CLI | GUI 提示改用 CLI | 是 |

## 已执行的验证

- macOS：Python 测试、Swift 测试、Tk 主界面启动与四语言导航、原生 App 构建和 `--self-test` 已执行。
- Windows/Linux：提供安装脚本、Tk 客户端、PyInstaller 构建入口和 CI 矩阵；**未在真实目标系统执行原生运行验收**。
- 平台分支单元测试覆盖 Windows 进程分类、`.cmd` 包装、非 macOS 桌面发现、文件锁和路径处理。

## 未验证事项

- Windows 官方 ChatGPT 桌面 App 的商店/MSIX 安装路径、进程控制和深链接。
- Linux 各发行版的 Tk 包、显示服务器和桌面集成差异。
- Windows/Linux 独立产物的真实安装、卸载、权限和杀毒软件兼容性。
- Apple 公证、Windows 代码签名和 Linux 包管理器分发。

发布说明必须继续标注这些限制，直到目标系统有实际验收记录。
