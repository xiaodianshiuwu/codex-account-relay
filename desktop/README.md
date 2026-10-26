# macOS SwiftUI App

唯一支持平台：macOS 14+。完整安装、配置、守护和验证边界见[主 README](../README.md)。

从仓库根目录：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install pyinstaller
.venv/bin/python tools/build_cli_bundle.py
./desktop/build.sh
swift test --package-path desktop
```

结果为 `desktop/build/Codex Relay.app`。构建不结束应用、不自动安装、不启动新应用；缺少内置后端或签名失败会报错。需要 Swift 6 工具链。

设置 `CODEX_RELAY_GUI_APP` 可复制到不存在的新输出目录；设置 `CODEX_RELAY_OPEN_AFTER=1` 才会显式启动。运行前确认自动配置，避免意外启动已启用的接力。离屏截图及发布流程见[开发说明](../docs/DEVELOPMENT.md)。

若源码位于 iCloud/File Provider 同步目录，系统可能自动给 App 添加 Finder 元数据，导致严格签名校验失败。可用 `CODEX_RELAY_BUILD_DIR=/tmp/codex-relay-build ./desktop/build.sh` 将组装与签名放在同步目录之外；不要跳过签名校验。
