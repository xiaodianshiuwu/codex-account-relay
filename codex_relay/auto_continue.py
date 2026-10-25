"""Send a continuation prompt into a resumed Codex thread.

This uses the public `codex exec resume` command, so it does not depend on
Electron internals. The command runs non-interactively and writes the result
to the same thread; the caller decides whether that is appropriate.
"""
from __future__ import annotations

from typing import Optional

from .i18n import Translator
from .util import RelayError, redact, run_capture, which_codex


def continue_thread(
    thread_id: str,
    prompt: str,
    *,
    timeout: float = 300.0,
    translator: Optional[Translator] = None,
) -> str:
    tr = translator or Translator()
    codex = which_codex()
    if not codex:
        raise RelayError(tr.t("找不到 codex CLI，请先安装 Codex 或设置 CODEX_RELAY_CODEX"))
    if not thread_id:
        raise RelayError(tr.t("没有可续跑的会话"))
    result = run_capture(
        [codex, "exec", "resume", "--json", "--skip-git-repo-check", thread_id, prompt],
        timeout=timeout,
    )
    output = (result.stdout or "").strip()
    if result.returncode != 0:
        detail = redact((result.stderr or output or tr.t("未知错误"))[:800])
        raise RelayError(tr.t("续跑失败（退出码 {code}）：{detail}", code=result.returncode, detail=detail))
    return output
