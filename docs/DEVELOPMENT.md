# 开发与验证

当前产品定义见 [PRD](PRD.md)。主分支仅发布 macOS 14+ SwiftUI App；历史 Tk/其他平台代码保留。

## 构建

需要 macOS、Swift 6 工具链、Python 3.11（Release 构建）和 PyInstaller。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install pyinstaller
.venv/bin/python tools/build_cli_bundle.py
./desktop/build.sh
```

App 输出到 `desktop/build/Codex Relay.app`。缺少可执行内置后端或签名验证失败时构建失败。默认不安装、不关闭已有进程、不自动打开 App。`CODEX_RELAY_GUI_APP` 可指定不存在的新输出路径，`CODEX_RELAY_OPEN_AFTER=1` 才显式打开。

## 测试

```sh
python3 tests/run.py
swift test --package-path desktop
python3 tools/build_strings.py --check
python3 tools/i18n_audit.py --json
```

Python 总入口隔离 Relay/Codex 数据目录，并设置 `CODEX_RELAY_TEST_MODE=1`。历史 Tk 运行库或显示不可用时跳过对应界面测试。守护测试使用本机假 socket、伪 SSH 帧和确定性状态机，不对业务聊天执行中断。

关键回归覆盖账号并发写入、精确认证身份、备份保留与回滚、未知额度、自动配置迁移、原聊天中断与续跑回执、模型/权限变化、消息去重、本机/SSH 守护及同一打包后端的后台进程启动。

## 离屏界面审查

```sh
CODEX_RELAY_HOME=/tmp/relay-preview-data \
CODEX_RELAY_PREFS_SUITE=io.codexrelay.review \
"desktop/build/Codex Relay.app/Contents/MacOS/CodexRelay" \
  --render-review /tmp/relay-preview-images
```

仅使用内置虚构数据，渲染四语言切换确认页及浅深两种守护页，不打开正常应用生命周期、不检查真实账号、不启用监控。预览必须同时指定隔离数据和偏好域。

## Release

版本同步更新 `codex_relay/__init__.py`、`pyproject.toml`、`setup.py`、`desktop/Info.plist` 和 App 的版本回退值。tag 必须与包版本一致。发布 workflow 先验证 Python、Swift、翻译和 tag，再构建 Python 3.11 内置后端、原生 App、校验签名，最终发布且仅发布：

- `CodexRelay-<tag>-macOS-SwiftUI.zip`
- `SHA256SUMS.txt`

下载最终 GitHub 资产后重新校验 SHA-256、后端版本、App 自检和守护入口；源码测试不能代替最终资产检查。不要将真实账号数据、监控状态、聊天记录或截图提交 Git。

若源码位于 iCloud/File Provider 同步目录，系统可能自动给 App 添加 Finder 元数据，导致严格签名校验失败。可用 `CODEX_RELAY_BUILD_DIR=/tmp/codex-relay-build ./desktop/build.sh` 将组装与签名放在同步目录之外；不要跳过签名校验。
