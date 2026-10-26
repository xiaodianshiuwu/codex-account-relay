"""Personal threshold policy. Pure decisions, no model calls or credential access."""
from dataclasses import dataclass
import hashlib
import json
import math

from .upstream_policy import latest_turn, quota_failure, UnsupportedState


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


@dataclass
class Quota:
    known: bool
    low: bool
    ready: bool
    next_check: float
    reason: str
    windows: list


def quota(response, now, threshold=5, resume_remaining=10, limit_id='codex'):
    if not isinstance(response, dict):
        return Quota(False, False, False, now + 60, '额度响应不是可识别的数据', [])
    buckets = response.get('rateLimitsByLimitId')
    if isinstance(buckets, dict) and buckets:
        bucket = buckets.get(limit_id)
    else:
        bucket = response.get('rateLimits')
        if isinstance(bucket, dict) and bucket.get('limitId') not in (None, limit_id):
            bucket = None
    def unknown(reason):
        return Quota(False, False, False, now + 60, reason, [])
    if not isinstance(bucket, dict):
        return unknown('缺少所选额度桶，不能判断可用额度')
    if bucket.get('spendControlReached') or bucket.get('individualLimit') is not None:
        return unknown('存在额外账户限制，需在 Codex 中检查')
    reached = bucket.get('rateLimitReachedType')
    if reached not in (None, 'rate_limit_reached'):
        return unknown('存在非窗口额度限制')
    if not isinstance(bucket.get('primary'), dict):
        return unknown('缺少主窗口数据')
    def parse_window(name):
        w = bucket.get(name)
        if not isinstance(w, dict):
            return None
        used, reset, duration = (w.get(k) for k in ('usedPercent', 'resetsAt', 'windowDurationMins'))
        if (not all(number(v) for v in (used, reset, duration)) or
                not 0 <= used <= 100 or reset <= 0 or duration <= 0):
            return None
        return {'name': name, 'remaining': 100 - used, 'resetsAt': reset,
                'windowDurationMins': duration}

    primary = parse_window('primary')
    if primary is None:
        return unknown('小时额度数据缺失或无效')
    if primary['windowDurationMins'] >= 1440:
        return unknown('主窗口不是可识别的小时额度，暂不操作')
    if primary['resetsAt'] <= now:
        return unknown('小时额度重置时间已过期，等待服务刷新')
    windows = [primary]
    secondary = parse_window('secondary')
    if secondary is not None and secondary['resetsAt'] > now:
        windows.append(secondary)  # Display only; never controls the local threshold or wait time.
    low = primary['remaining'] <= threshold
    # An explicit service rejection still prevents dispatch. It does not trigger
    # a proactive interrupt and is rechecked without waiting for a weekly reset.
    ready = not reached and primary['remaining'] >= resume_remaining
    next_check = primary['resetsAt'] + 15 if primary['remaining'] < resume_remaining else now + 60
    return Quota(True, low, ready, next_check,
                 '小时额度达到暂停阈值' if low else ('小时额度已恢复' if ready else
                 ('服务器仍报告额度限制，等待状态刷新' if reached else '小时额度尚未达到续跑阈值')), windows)


@dataclass
class Task:
    kind: str
    reason: str
    turn_id: str = ''
    status: str = ''


def task(state, host_id='local'):
    if not isinstance(state, dict):
        return Task('blocked', '聊天状态格式不兼容')
    if state.get('hostId') != host_id:
        return Task('blocked', '聊天主机与当前选择不一致，请重新选择正确主机')
    if state.get('ephemeral') is True:
        return Task('blocked', '临时聊天不支持自动监控')
    if state.get('ephemeral') is not False:
        return Task('blocked', '暂未核验聊天持久性，请在 Codex 中打开原聊天后刷新')
    if host_id != 'local' and (not host_id.startswith('remote-ssh-') or
            state.get('canAcceptDirectInput') is not True or not state.get('latestModel')):
        return Task('blocked', 'SSH 聊天不允许直接输入，或缺少执行模型信息')
    if state.get('resumeState') != 'resumed':
        return Task('connecting', '等待原聊天加载完成')
    if not isinstance(state.get('requests'), list):
        return Task('blocked', '缺少审批和用户输入状态')
    if state['requests'] or state.get('queuedFollowUps') or state.get('threadGoalResumeConfirmation'):
        return Task('blocked', '聊天需要用户处理审批、输入或排队消息')
    goal = state.get('threadGoal')
    if goal is not None and (not isinstance(goal, dict) or goal.get('status') != 'active'):
        return Task('blocked', '聊天目标已暂停、结束或需要用户处理')
    runtime = state.get('threadRuntimeStatus')
    if not isinstance(runtime, dict):
        return Task('blocked', '缺少桌面端实时运行状态')
    if runtime.get('type') == 'active':
        if not isinstance(runtime.get('activeFlags'), list):
            return Task('blocked', '缺少执行中的等待条件状态')
        if runtime['activeFlags']:
            return Task('blocked', '聊天正在等待用户操作')
    try:
        turn = latest_turn(state)
    except (UnsupportedState, AttributeError, TypeError, KeyError) as exc:
        return Task('blocked', str(exc))
    tid, status = turn.get('turnId'), turn.get('status')
    if not isinstance(tid, str) or not tid:
        return Task('blocked', '缺少稳定轮次标识')
    if runtime.get('type') not in ('active', 'idle', 'systemError'):
        return Task('connecting', '等待桌面端状态同步', tid, status)
    if ((status == 'inProgress' and runtime.get('type') != 'active') or
            (status != 'inProgress' and runtime.get('type') == 'active')):
        return Task('connecting', '轮次与运行状态尚未同步，暂不操作', tid, status)
    if status == 'inProgress':
        return Task('running', '正在执行', tid, status)
    if status == 'failed' and quota_failure(turn):
        return Task('quota_error', '有结构化额度耗尽错误', tid, status)
    if status == 'interrupted':
        return Task('interrupted', '本轮已中断', tid, status)
    if status == 'completed':
        if goal is not None:
            return Task('goal_wait', '等待 Codex 的目标继续执行', tid, status)
        return Task('finished', '本轮正常结束，停止监控', tid, status)
    return Task('blocked', '非额度错误或未知轮次状态', tid, status)


def identity(state):
    """Hash work/settings without persisting conversation content or token counters."""
    data = {k: state.get(k) for k in (
        'id', 'hostId', 'cwd', 'latestModel', 'latestReasoningEffort', 'latestCollaborationMode',
        'currentPermissions', 'latestThreadSettings', 'threadGoal', 'daybreakEnabled', 'modelProvider',
        'remoteBinding', 'remoteConnectionKey')}
    turn = latest_turn(state)
    data['turnId'] = turn.get('turnId')
    data['params'] = turn.get('params')
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
