"""Validated personal settings. Shared by CLI, GUI, and frozen monitor starts."""
import json
import math
import os
from pathlib import Path
import tempfile

DEFAULT_MESSAGE = ('额度恢复后，请继续本会话原有的未完成工作。先核对当前进度与工作区，'
                   '不要重复已完成步骤，不扩大范围，不绕过审批。若工作已经完成或需要用户决定，请明确说明并停止。')
DEFAULTS = {'threshold': 5.0, 'resumeRemaining': 10.0, 'interval': 30.0, 'continuation': DEFAULT_MESSAGE}


def validate(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError('设置格式不正确')
    result = dict(DEFAULTS, **value)
    for key in ('threshold', 'resumeRemaining', 'interval'):
        n = result[key]
        if type(n) not in (int, float) or not math.isfinite(n):
            raise ValueError('阈值和间隔必须是有限数字')
    if not 0 <= result['threshold'] < result['resumeRemaining'] <= 100:
        raise ValueError('要求：0 ≤ 停止阈值 < 恢复阈值 ≤ 100')
    if result['interval'] <= 0:
        raise ValueError('检查间隔必须为有限正数')
    text = result['continuation']
    if not isinstance(text, str) or not text.strip() or '\0' in text:
        raise ValueError('续跑文字不能为空，不能包含空字符')
    result['continuation'] = text.strip()
    return result


def load(directory):
    path = Path(directory) / 'settings.json'
    return validate(json.loads(path.read_text())) if path.exists() else dict(DEFAULTS)


def write(path, value):
    result = validate(value)
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.settings-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
    return result
