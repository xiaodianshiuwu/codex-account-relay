# Security Policy

## 报告安全问题

请使用 GitHub 仓库的 **Private vulnerability reporting / Security advisory** 功能，不要在公开 Issue 中粘贴 token、auth.json、cookie、账号邮箱、thread ID 或完整日志。

如果必须提供日志，请先删除：

- `Authorization`、cookie、access/refresh token；
- `auth.json`、`state_*.sqlite`、rollout/session 文件；
- 本机用户名、home 路径、设备名和账号标识。

## 凭据泄露处理

如果真实凭据曾经进入 Git 历史、Release、日志或截图，仅删除文件不够：请立即撤销/轮换该凭据，并检查相关账号的会话和 API key。维护者会评估是否需要清理历史或撤回 Release。

## 支持范围

只支持当前默认分支和最近一个 Release。第三方 Codex/ChatGPT 版本变化导致的问题会尽量兼容，但不承诺无限期支持旧版本。
