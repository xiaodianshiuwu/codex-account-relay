"""Planning estimates only: never authorizes a monitor mutation."""
from .policy import quota


def forecast(response, now, threshold=5, resume_remaining=10, count=4):
    q = quota(response, now, threshold, resume_remaining)
    result = {'known': q.known, 'reason': q.reason, 'asOf': now, 'slots': [],
              'notice': '后续窗口按立即持续使用估算；空闲、服务调整与周额度会影响实际可用时间，以刷新后的接口为准。'}
    if not q.known: return result
    primary = q.windows[0]
    duration = primary['windowDurationMins'] * 60
    reset = primary['resetsAt']
    result.update(nextReset=reset, durationMinutes=primary['windowDurationMins'], readyNow=q.ready,
                  currentRemaining=primary['remaining'])
    for index in range(max(0, count)):
        start = reset + index * duration
        result['slots'].append({'start': start, 'end': start + duration,
                                'resetConfirmed': index == 0,
                                'label': '接口重置时间' if index == 0 else '预计下一轮'})
    return result
