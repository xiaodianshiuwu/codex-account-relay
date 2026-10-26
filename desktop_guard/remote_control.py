"""Control the selected loaded SSH thread through its existing daemon only."""
import time
import uuid

from .desktop import AppError, ConnectionUnavailable, CONTINUATION, NotDispatched
from .remote import RemoteReadOnlyServer, connection_key


class RemoteController(RemoteReadOnlyServer):
    def __init__(self, host, timeout=15, expected_key=None, keep_alive=False):
        super().__init__(host, timeout)
        self.expected_key = expected_key
        self.thread_id = None
        self.generation = 0
        self.pending_request = False
        self.keep_alive = keep_alive
        self.subscribed = False

    def __enter__(self):
        if self.expected_key and connection_key(self.host) != self.expected_key:
            raise AppError('SSH 主机连接设置已改变，请重新选择监控')
        if self.process is not None:
            if self.process.poll() is None:
                return self
            self.close()
        try:
            super().__enter__()
            return self
        except Exception:
            self.close()
            raise

    def close(self):
        super().__exit__(None, None, None)
        self.process = None
        self.selector = None
        self.buffer = b''
        self.subscribed = False
        self.pending_request = False

    def __exit__(self, exc_type, exc, tb):
        if not self.keep_alive or (exc_type is not None and issubclass(exc_type, (ConnectionUnavailable, TimeoutError, OSError))):
            self.close()

    def _observe(self, message):
        params = message.get('params', {})
        if not isinstance(params, dict):
            return
        thread = params.get('thread')
        target = params.get('threadId') or params.get('conversationId') or (thread.get('id') if isinstance(thread, dict) else None)
        if target != self.thread_id:
            return
        method = message.get('method', '')
        if 'id' in message and method:
            # No approval/tool/user-input responses. The desktop remains the handler.
            self.pending_request = True
            self.generation += 1
        elif method and not any(part in method for part in ('delta', 'tokenUsage', 'reasoning')):
            self.generation += 1

    def _request(self, method, params=None):
        # Unlike the generic read client, never error-answer an approval request.
        self.counter += 1
        request_id = self.counter
        message = {'method': method, 'id': request_id}
        if params is not None:
            message['params'] = params
        deadline = self.deadline()
        self._send(message)
        while True:
            reply = self._line(deadline)
            self._observe(reply)
            if reply.get('id') != request_id or 'method' in reply:
                continue
            if 'error' in reply:
                raise AppError('远程 Codex 拒绝请求；不会自动重发，请检查原聊天')
            if 'result' not in reply:
                raise AppError('远程 Codex 响应格式不兼容')
            return reply['result']

    def snapshot(self, thread_id):
        with self.bounded(16):
            return self._snapshot(thread_id)

    def _snapshot(self, thread_id):
        self.thread_id = str(uuid.UUID(thread_id))
        summary = self.query('thread/read', {'threadId': self.thread_id, 'includeTurns': False}).get('thread')
        if not isinstance(summary, dict) or summary.get('id') != self.thread_id:
            raise AppError('SSH 返回了不匹配的聊天状态')
        execution_settings = None
        runtime_summary = summary.get('status')
        if not isinstance(runtime_summary, dict):
            raise AppError('SSH 聊天缺少运行状态')
        runtime_before = runtime_summary.get('type')
        if self.keep_alive and runtime_before in ('active', 'idle', 'systemError'):
            # For a loaded thread, resume with no overrides attaches this client
            # and returns the actual live settings. Keep the subscription alive
            # through quota waits so an idle original thread is not unloaded.
            # Never call this on an unloaded thread: that can apply CLI defaults.
            loaded = self._request('thread/resume', {'threadId': self.thread_id, 'excludeTurns': True})
            loaded_thread = loaded.get('thread') if isinstance(loaded, dict) else None
            if not isinstance(loaded_thread, dict) or loaded_thread.get('id') != self.thread_id:
                raise AppError('SSH 订阅没有返回匹配的原聊天执行设置')
            if loaded_thread.get('sessionId') != summary.get('sessionId'):
                raise ConnectionUnavailable('SSH 原聊天会话正在切换，等待重新核对')
            if loaded.get('cwd') != loaded_thread.get('cwd') or loaded.get('model') != loaded_thread.get('model'):
                raise ConnectionUnavailable('SSH 执行设置正在切换，等待重新核对')
            self.subscribed = True
            execution_settings = {k: loaded.get(k) for k in ('approvalPolicy', 'approvalsReviewer', 'sandbox',
                'activePermissionProfile', 'collaborationMode', 'runtimeWorkspaceRoots', 'disabledPluginIds',
                'multiAgentMode', 'serviceTier')}
        turns = self.query('thread/turns/list', {'threadId': self.thread_id, 'limit': 1,
                         'sortDirection': 'desc', 'itemsView': 'notLoaded'}).get('data')
        if isinstance(turns, list) and not turns:
            raise AppError('这个聊天还没有轮次，请先在 Codex 中开始任务')
        if not isinstance(turns, list) or len(turns) != 1 or not isinstance(turns[0], dict):
            raise AppError('SSH 聊天缺少可核验的最新轮次')
        goal_reply = self.query('thread/goal/get', {'threadId': self.thread_id})
        queue = self.query('thread/queue/list', {'threadId': self.thread_id, 'limit': 1})
        if not isinstance(goal_reply, dict) or 'goal' not in goal_reply or not isinstance(queue.get('data'), list):
            raise AppError('SSH 聊天缺少目标或排队消息状态')
        # Runtime and persisted turn information are separate reads. A second
        # metadata/turn read prevents using a tail that changed during collection.
        again = self.query('thread/read', {'threadId': self.thread_id, 'includeTurns': False}).get('thread')
        tail = self.query('thread/turns/list', {'threadId': self.thread_id, 'limit': 1,
                        'sortDirection': 'desc', 'itemsView': 'notLoaded'}).get('data')
        stable_keys = ('id', 'sessionId', 'model', 'modelProvider', 'reasoningEffort', 'cwd', 'ephemeral',
                       'canAcceptDirectInput', 'daybreakEnabled', 'environments', 'extra')
        if (not isinstance(again, dict) or not isinstance(tail, list) or len(tail) != 1 or
                any(summary.get(k) != again.get(k) for k in stable_keys) or
                not isinstance(tail[0], dict) or turns[0].get('id') != tail[0].get('id')):
            raise ConnectionUnavailable('SSH 聊天状态正在切换，等待下一次检查')
        runtime = again.get('status')
        if not isinstance(runtime, dict):
            raise AppError('SSH 聊天缺少实时运行状态')
        turn = tail[0]
        if not isinstance(turn.get('id'), str) or not turn['id']:
            raise AppError('SSH 聊天缺少轮次 ID')
        # Normalize only the fields used by the decision policy. No full history,
        # messages, credentials or explicit settings overrides are persisted.
        return {'id': self.thread_id, 'hostId': self.host['id'], 'ephemeral': again.get('ephemeral'),
                'resumeState': 'resumed' if runtime.get('type') != 'notLoaded' else 'resuming',
                'requests': ['unhandled-request'] if self.pending_request else [],
                'queuedFollowUps': queue['data'] or ([True] if queue.get('nextCursor') else []),
                'threadGoal': goal_reply['goal'], 'threadRuntimeStatus': runtime,
                'latestModel': again.get('model'), 'modelProvider': again.get('modelProvider'),
                'latestReasoningEffort': again.get('reasoningEffort'), 'cwd': again.get('cwd'),
                'latestThreadSettings': execution_settings,
                'daybreakEnabled': again.get('daybreakEnabled'),
                'remoteBinding': {k: again.get(k) for k in ('sessionId', 'environments', 'extra')},
                'remoteConnectionKey': connection_key(self.host),
                'canAcceptDirectInput': again.get('canAcceptDirectInput'),
                'turns': [{'turnId': turn['id'], 'status': turn.get('status'), 'error': turn.get('error'),
                           'params': {'rootTurnId': turn.get('rootTurnId')}}]}

    def _drain(self):
        deadline = self.deadline()
        for _ in range(128):
            if not self.buffer and not self.selector.select(0):
                return
            self._observe(self._line(deadline))
        raise NotDispatched('SSH 状态仍在变化，取消本次操作')

    def _validate(self, thread_id, validate, dispatch_guard):
        try:
            state = self.snapshot(thread_id)
            if not validate(state):
                raise NotDispatched('SSH 操作前任务状态已改变，请检查原聊天')
            generation = self.generation
            self._drain()
            if self.generation != generation or self.pending_request:
                raise NotDispatched('SSH 操作前收到新状态，请稍后重查')
            if dispatch_guard is not None and not dispatch_guard():
                raise NotDispatched('监控已取消，未发送操作')
            return state
        except (ConnectionUnavailable, TimeoutError, OSError) as exc:
            raise NotDispatched('SSH 操作前连接不可用，未发送操作') from exc
        except AppError as exc:
            raise NotDispatched(str(exc)) from exc

    def interrupt(self, thread_id, turn_id, validate, dispatch_guard=None):
        with self.bounded(30):
            return self._interrupt(thread_id, turn_id, validate, dispatch_guard)

    def _interrupt(self, thread_id, turn_id, validate, dispatch_guard=None):
        self._validate(thread_id, validate, dispatch_guard)
        reply = self._request('turn/interrupt', {'threadId': thread_id, 'turnId': turn_id})
        if reply != {}:
            raise AppError('SSH 中断响应格式不兼容，请检查原聊天')
        # {} acknowledges cancellation dispatch, not completion. Require the
        # same turn to reach interrupted before allowing recovery ownership.
        deadline = self.operation_deadline if self.operation_deadline is not None else self.deadline()
        while time.monotonic() < deadline:
            state = self.snapshot(thread_id)
            turn = state['turns'][0]
            if turn['turnId'] != turn_id:
                raise AppError('SSH 中断后最新轮次改变，结果需人工核对')
            if turn['status'] == 'interrupted':
                return turn_id
            if turn['status'] != 'inProgress':
                raise AppError('SSH 本轮没有进入已中断状态，不能自动续跑')
            time.sleep(0.2)
        raise AppError('SSH 中断终止状态尚未确认，请检查原聊天')

    def resume(self, thread_id, message_id, validate, dispatch_guard=None):
        with self.bounded(30):
            return self._resume(thread_id, message_id, validate, dispatch_guard)

    def _resume(self, thread_id, message_id, validate, dispatch_guard=None):
        state = self._validate(thread_id, validate, dispatch_guard)
        turn = state['turns'][0]
        params = {'threadId': thread_id, 'clientUserMessageId': message_id,
                  'parentTurnId': turn['turnId'],
                  'input': [{'type': 'text', 'text': getattr(self, 'continuation', CONTINUATION), 'text_elements': []}]}
        root = turn['params'].get('rootTurnId')
        if root:
            params['rootTurnId'] = root
        # The thread is already loaded in the original daemon. Omitting model,
        # cwd, permissions and collaboration overrides inherits its live settings.
        result = self._request('turn/start', params)
        turn_id = result.get('turn', {}).get('id') if isinstance(result, dict) else None
        if not isinstance(turn_id, str) or not turn_id or turn_id == turn['turnId']:
            raise AppError('SSH 续跑响应缺少新轮次确认，请检查原聊天')
        return turn_id
