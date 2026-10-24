# Contributing

感谢你改进 Codex Relay。

## 提交前

1. 先搜索已有 Issue/PR，避免重复。
2. 从 `main` 创建分支。
3. 修改代码时补充或更新测试；用户可见文案必须同时更新四种语言。
4. 运行：

```sh
python3 tests/run.py
python3 tools/build_strings.py --check
python3 tools/i18n_audit.py --json
swift test --package-path desktop --scratch-path /tmp/codex-relay-swift
git diff --check
```

5. 确认 diff 中没有凭据、邮箱、主目录路径、auth/session 数据、日志或构建产物。

## Pull Request

请说明问题、修改行为、验证命令和未验证的平台。界面改动请附脱敏截图；不要上传真实账号、邮箱、thread ID 或本机路径。

## 安全

发现凭据泄露或安全问题请不要公开 Issue，按 `SECURITY.md` 的私密渠道报告。
