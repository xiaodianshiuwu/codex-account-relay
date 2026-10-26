"""Upstream history and structured quota-error helpers; see third-party notice."""


class UnsupportedState(ValueError):
    pass


def latest_turn(state):
    history = state.get('turnHistory')
    if isinstance(history, dict) and history.get('kind') == 'canonical':
        hist = history.get('history', {})
        islands = hist.get('islands', [])
        if not islands:
            raise UnsupportedState('没有可验证的最新会话历史')
        tail = islands[-1]
        if tail.get('newerBoundary', {}).get('status') != 'exhausted':
            raise UnsupportedState('历史尾部不完整；不能判断最新一轮')
        entries = tail.get('entries', [])
        if not entries:
            raise UnsupportedState('最新历史片段为空')
        turn = hist.get('entitiesByKey', {}).get(entries[-1].get('value'))
    else:
        turns = state.get('turns')
        if not isinstance(turns, list) or not turns:
            raise UnsupportedState('缺少会话轮次')
        turn = turns[-1]
    if not isinstance(turn, dict):
        raise UnsupportedState('不支持的会话历史结构')
    return turn


def quota_failure(turn):
    error = turn.get('error')
    if not isinstance(error, dict):
        return False
    info = error.get('codexErrorInfo')
    # These are protocol enum values, not a fuzzy match on user/assistant text.
    return info in ('usageLimitExceeded', 'UsageLimitExceeded') if isinstance(info, str) else False
