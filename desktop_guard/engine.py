"""Threshold interrupt -> wait for fresh quota -> continue original conversation."""
import time
import uuid

from .desktop import AppError, ConnectionUnavailable, NotDispatched
from .policy import task, quota, identity
from .diagnostics import diagnostic
from .settings import DEFAULT_MESSAGE


class Engine:
    def __init__(self, store, desktop_factory, reader, threshold=5, resume_remaining=10,
                 limit_id='codex', max_resumes=0, clock=time.time, continuation=DEFAULT_MESSAGE):
        self.store, self.desktop_factory, self.reader = store, desktop_factory, reader
        self.threshold, self.resume_remaining = threshold, resume_remaining
        self.limit_id, self.max_resumes, self.clock = limit_id, max_resumes, clock
        self.next_quota_check = 0
        self.continuation = continuation

    def quota(self):
        return quota(self.reader(), self.clock(), self.threshold, self.resume_remaining, self.limit_id)

    def end(self, phase, reason):
        self.store.end(phase, reason)
        return False

    def update(self, reason, **changes):
        self.store.save(reason=reason, **changes)
        return True

    def task(self, state):
        return task(state, self.store.host_id)

    def step(self):
        # Persistent intent survives crashes; never re-send an uncertain mutation.
        if any(a.get('status') == 'intent' for a in self.store.data['attempts'].values()):
            return self.end('uncertain', '上次操作结果未确认，请检查原聊天；不会自动重试')
        if not self.store.live():
            return self.end('stopped', '监控已取消，不再中断或发送续跑消息')
        tid = self.store.thread_id
        try:
            with self.desktop_factory() as app:
                app.continuation = self.continuation
                state = app.snapshot(tid)
                t = self.task(state)
                if self.store.host_id != 'local':
                    remote_binding = state.get('remoteBinding')
                    if not isinstance(remote_binding, dict) or not remote_binding.get('sessionId') or not state.get('remoteConnectionKey'):
                        raise AppError('SSH 状态缺少可核验的主机或会话绑定')
                    binding = {'hostKey': state['remoteConnectionKey'],
                               'sessionId': remote_binding['sessionId']}
                    saved = self.store.data.get('remoteBinding')
                    if saved is not None and saved != binding:
                        return self.end('attention', 'SSH 主机连接或原聊天会话已改变，请重新选择监控')
                    if saved is None:
                        self.store.save(remoteBinding=binding)
                if t.kind == 'blocked':
                    return self.end('attention', t.reason)
                if t.kind == 'connecting':
                    return self.update(t.reason)
                paused = self.store.data.get('paused')
                if paused:
                    if paused.get('cause') == 'threshold':
                        receipt = self.store.data['attempts'].get('interrupt:' + paused['turnId'], {})
                        if receipt.get('status') != 'confirmed' or receipt.get('resultId') != paused['turnId']:
                            return self.end('attention', '缺少匹配的中断确认，不能认定这轮由本工具暂停')
                    if t.turn_id != paused['turnId'] or identity(state) != paused['identity']:
                        return self.end('attention', '等待期间聊天轮次或执行设置改变，请重新选择监控')
                    if t.kind == 'finished' or t.kind == 'goal_wait':
                        return self.end('finished', '被监控轮次已正常结束，不再续跑')
                    if t.kind == 'running':
                        if self.clock() - paused['at'] > 120:
                            return self.end('attention', '中断已确认，但两分钟内未同步到终止状态')
                        return self.update('等待桌面端确认中断状态')
                    expected = 'interrupted' if paused['cause'] == 'threshold' else 'quota_error'
                    if t.kind != expected:
                        return self.end('attention', '等待期间终止原因改变，不再自动续跑')
                    if self.max_resumes and self.store.data['resumeCount'] >= self.max_resumes:
                        return self.end('attention', '达到配置的累计续跑次数上限')
                    if self.clock() < self.next_quota_check:
                        return self.update('等待小时额度恢复；最多 60 秒重读额度，持续检查原聊天')
                    q = self.quota()
                    self.store.data['windows'] = q.windows
                    if not q.ready:
                        self.next_quota_check = min(q.next_check, self.clock() + 60) if q.known else self.clock() + 60
                        return self.update(q.reason, phase='waiting', nextQuotaCheck=self.next_quota_check)
                    baseline = paused['identity']
                    message_id = str(uuid.uuid4())
                    key = self.store.claim('resume', t.turn_id, message_id)
                    try:
                        result = app.resume(tid, message_id,
                            lambda fresh: (self.task(fresh).kind == expected and
                                           self.task(fresh).turn_id == t.turn_id and identity(fresh) == baseline),
                            # q was freshly read this step; local cancellation remains the final check.
                            self.store.live)
                        self.store.data['attempts'][key].update(status='confirmed', resultId=result)
                        self.store.save(paused=None, phase='watching', lastResumedTurn=result,
                                        lastContinuationTurn=result,
                                        graceUntil=self.clock() + 120,
                                        resumeCount=self.store.data['resumeCount'] + 1,
                                        reason='桌面端已确认续跑，继续监控原聊天')
                        self.next_quota_check = 0
                        return True
                    except NotDispatched:
                        self.store.data['attempts'][key]['status'] = 'not_sent'
                        return self.end('attention', '续跑前状态改变，消息未发送，请检查原聊天')
                    except Exception as exc:
                        self.store.save(lastError=diagnostic(exc, 'resume/confirmation'))
                        return self.end('uncertain', '续跑操作结果未确认，请检查原聊天；不会自动重发')

                # Allow acknowledged turn/start to become visible in the snapshot.
                last_resumed = self.store.data.get('lastResumedTurn')
                if last_resumed and t.turn_id != last_resumed and self.clock() < self.store.data.get('graceUntil', 0):
                    return self.update('续跑已确认，等待新轮次同步')
                if last_resumed and t.turn_id != last_resumed:
                    return self.end('attention', '续跑确认与桌面端最新轮次不匹配，请检查原聊天')
                if last_resumed:
                    self.store.data.pop('lastResumedTurn', None)
                    self.store.data.pop('graceUntil', None)
                if t.kind == 'finished':
                    return self.end('finished', t.reason)
                if t.kind == 'interrupted':
                    return self.end('stopped', '轮次由用户或其他来源停止，不会自动重启')
                if t.kind == 'goal_wait':
                    return self.update(t.reason)
                if t.kind == 'quota_error':
                    if self.store.data.get('lastContinuationTurn') == t.turn_id:
                        q = self.quota()
                        self.store.data['windows'] = q.windows
                        if q.ready:
                            return self.end('attention', '续跑后服务器再次拒绝额度，小时数据未能解释此限制；请检查原聊天，不会循环发送')
                    self.store.save(paused={'turnId': t.turn_id, 'identity': identity(state),
                                            'cause': 'quota_error', 'at': self.clock()},
                                    phase='waiting', reason='额度耗尽，等待账户额度恢复')
                    self.next_quota_check = 0
                    return True
                q = self.quota()
                self.store.data['windows'] = q.windows
                if not q.known or not q.low:
                    return self.update(q.reason if not q.known else '正在执行，小时额度高于暂停阈值', phase='watching')
                if self.max_resumes and self.store.data['resumeCount'] >= self.max_resumes:
                    return self.end('attention', '续跑次数已用完，停止监控；没有中断当前工作')
                baseline = identity(state)
                self.store.save(paused={'turnId': t.turn_id, 'identity': baseline,
                                        'cause': 'threshold', 'at': self.clock()},
                                phase='interrupting', reason='额度达到阈值，准备中断当前轮次')
                key = self.store.claim('interrupt', t.turn_id, str(uuid.uuid4()))
                try:
                    result = app.interrupt(tid, t.turn_id,
                        lambda fresh: (self.task(fresh).kind == 'running' and
                                       self.task(fresh).turn_id == t.turn_id and identity(fresh) == baseline),
                        self.store.live)
                    self.store.data['attempts'][key].update(status='confirmed', resultId=result)
                    self.store.save(phase='waiting', reason='中断已确认，等待额度恢复')
                    self.next_quota_check = 0
                    return True
                except NotDispatched:
                    self.store.data['attempts'][key]['status'] = 'not_sent'
                    # No request was written. Never label someone else's interruption as our own.
                    self.store.save(paused=None, phase='watching', reason='桌面状态正在变化，中断请求未发送，稍后重新检查')
                    return self.store.live()
                except Exception as exc:
                    self.store.save(lastError=diagnostic(exc, 'interrupt/confirmation'))
                    return self.end('uncertain', '中断操作结果未确认，请检查原聊天；不会自动重试')
        except (ConnectionUnavailable, ConnectionError, FileNotFoundError, TimeoutError):
            return self.update('连接暂不可用，等待 Codex 或网络恢复；不会发送消息')
        except AppError as exc:
            return self.end('attention', str(exc))
