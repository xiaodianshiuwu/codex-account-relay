"""Desktop IPC with runtime validation; never owns threads or answers approvals."""
import json
import os
from pathlib import Path
import plistlib
import select
import socket
import stat
import struct
import subprocess
import time
import uuid
from .timing import BoundedQueries
from .upstream_policy import latest_turn

MAX_FRAME = 32 * 1024 * 1024
from .settings import DEFAULT_MESSAGE as CONTINUATION


class AppError(RuntimeError):
    pass


class NotDispatched(AppError):
    """Validation refused before any mutation request was written to the socket."""


class ConnectionUnavailable(AppError):
    """Transient loss of transport; never evidence authorizing a send."""


class ThreadUnavailable(ConnectionUnavailable):
    """App is connected, but the selected conversation is not loaded."""


def check_version(app_path):
    path = Path(app_path)
    try:
        info = plistlib.loads((path / 'Contents/Info.plist').read_bytes())
    except (OSError, ValueError) as exc:
        raise AppError('找不到可识别的 Codex App') from exc
    version = (info.get('CFBundleShortVersionString'), info.get('CFBundleVersion'))
    if info.get('CFBundleIdentifier') != 'com.openai.codex':
        raise AppError('所选应用不是可识别的 Codex App')
    return {'version': version[0], 'build': version[1]}


def open_selected_thread(app_path, thread_id):
    """Navigate to an existing selected conversation. Never prefill/send a prompt.

    The pinned App's localConversation deep-link handler checks thread existence
    then navigates to its original route. This does not make this client an owner.
    Success here only means navigation was requested; a fresh snapshot is required.
    """
    check_version(app_path)
    target = str(uuid.UUID(thread_id))
    subprocess.run(['/usr/bin/open', '-g', '-a', str(app_path), 'codex://threads/' + target],
                   check=True, timeout=5, stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def local_persistence(state, home):
    """IPC omits ephemeral. Verify the registered rollout using metadata only."""
    if 'ephemeral' in state:
        return state
    result = dict(state)
    result['ephemeral'] = None
    rollout = state.get('rolloutPath')
    session = state.get('sessionId') or state.get('id')
    try:
        session = str(uuid.UUID(session))
        path = Path(rollout)
        base = (Path(home) / 'sessions').resolve()
        resolved = path.resolve()
        metadata = path.lstat()
        if (path.is_absolute() and stat.S_ISREG(metadata.st_mode) and metadata.st_uid == os.getuid()
                and resolved.is_relative_to(base) and session in path.name):
            result.update(ephemeral=False, persistenceSource='registered-rollout')
    except (OSError, TypeError, ValueError, AttributeError):
        pass
    return result


class Desktop(BoundedQueries):
    def __init__(self, home, app_path='/Applications/ChatGPT.app', timeout=12, metadata_reader=None):
        self.home, self.app_path, self.timeout = Path(home), Path(app_path), timeout
        self.sock = None
        self.client_id = 'initializing-client'
        self.owner = None
        self.thread_id = None
        self.snapshot_value = None
        self.revision = None
        self.state_generation = 0
        self.archived = False
        self.dispatch_turn_id = None
        self.metadata_reader = metadata_reader

    def __enter__(self):
        check_version(self.app_path)
        path = self.home / 'ipc/ipc.sock'
        try:
            directory, endpoint = path.parent.lstat(), path.lstat()
            if not stat.S_ISDIR(directory.st_mode) or not stat.S_ISSOCK(endpoint.st_mode):
                raise AppError('IPC 路径不是普通目录中的 socket')
            if any(s.st_uid != os.getuid() or s.st_mode & 0o077 for s in (directory, endpoint)):
                raise AppError('IPC 权限或所有者不安全；不会自动修改权限')
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.settimeout(self.deadline() - time.monotonic())
            self.sock.connect(str(path))
            result = self.request('initialize', {'clientType': 'codex-auto-resume'}, 0)
            self.client_id = result['result']['clientId']
            return self
        except Exception:
            self.close()
            raise

    def close(self):
        if self.sock:
            if self.owner and self.thread_id:
                try:
                    self.follow(False)
                except (OSError, TimeoutError, AppError):
                    pass
            self.sock.close()
            self.sock = None

    def __exit__(self, *args):
        self.close()

    def _send(self, msg):
        data = json.dumps(msg, ensure_ascii=False).encode()
        if not 0 < len(data) <= MAX_FRAME:
            raise AppError('IPC 消息过大')
        self.sock.settimeout(self.deadline() - time.monotonic())
        self.sock.sendall(struct.pack('<I', len(data)) + data)

    def _exact(self, n, deadline):
        chunks, remaining = [], n
        while remaining:
            timeout = deadline - time.monotonic()
            if timeout <= 0:
                raise TimeoutError('App 响应超时')
            self.sock.settimeout(timeout)
            chunk = self.sock.recv(remaining)
            if not chunk:
                raise ConnectionUnavailable('App 连接已断开')
            chunks.append(chunk)
            remaining -= len(chunk)
        return b''.join(chunks)

    def _read(self, deadline):
        size = struct.unpack('<I', self._exact(4, deadline))[0]
        if not 0 < size <= MAX_FRAME:
            raise AppError('IPC 消息长度不受支持')
        msg = json.loads(self._exact(size, deadline))
        if not isinstance(msg, dict):
            raise AppError('IPC 消息不是对象')
        if msg.get('type') in ('client-discovery-request', 'request') and not isinstance(msg.get('requestId'), str):
            raise AppError('IPC 请求缺少有效标识')
        if msg.get('type') == 'client-discovery-request':
            self._send({'type': 'client-discovery-response', 'requestId': msg['requestId'],
                        'response': {'canHandle': False}})
        if msg.get('type') == 'request':
            # In particular: never become an approval handler or thread owner.
            self._send({'type': 'response', 'requestId': msg['requestId'],
                        'resultType': 'error', 'error': 'unsupported-by-auto-resume'})
        if msg.get('type') == 'broadcast' and msg.get('method') == 'thread-archived':
            params = msg.get('params', {})
            if not isinstance(params, dict):
                raise AppError('IPC 通知参数格式不兼容')
            if params.get('conversationId') == self.thread_id and params.get('hostId', 'local') == 'local':
                self.archived = True
        if msg.get('type') == 'broadcast' and msg.get('method') == 'thread-stream-state-changed':
            params = msg.get('params', {})
            if not isinstance(params, dict):
                raise AppError('IPC 通知参数格式不兼容')
            if params.get('conversationId') == self.thread_id and params.get('hostId') == 'local' and msg.get('sourceClientId') == self.owner:
                if msg.get('version') != 11:
                    raise AppError('App 状态协议版本已变化')
                self.state_generation += 1
                change = params.get('change', {})
                if change.get('type') == 'snapshot':
                    self.snapshot_value = change.get('conversationState')
                    self.revision = change.get('revision')
                elif change.get('type') == 'patches':
                    # Do not infer current state from a partial/missed patch stream.
                    self.snapshot_value = None
                else:
                    raise AppError('App 状态变更格式不受支持')
        return msg

    def request(self, method, params, version, target=None):
        request_id = str(uuid.uuid4())
        msg = {'type': 'request', 'requestId': request_id, 'sourceClientId': self.client_id,
               'method': method, 'params': params, 'version': version,
               'timeoutMs': int(self.timeout * 1000)}
        if target:
            msg['targetClientId'] = target
        deadline = self.deadline()
        self._send(msg)
        while True:
            reply = self._read(deadline)
            if reply.get('type') != 'response' or reply.get('requestId') != request_id:
                continue
            if reply.get('resultType') != 'success':
                # Only emit safe error codes, never raw remote error strings.
                code = reply.get('error')
                if code == 'no-client-found':
                    raise ThreadUnavailable('所选对话暂未连接到 App')
                raise AppError('App 拒绝请求或接口不兼容；未自动重试')
            if reply.get('method') != method:
                raise AppError('App 响应方法不匹配')
            return reply

    def follow(self, enabled):
        self._send({'type': 'broadcast', 'method': 'thread-stream-following-changed',
                    'sourceClientId': self.client_id, 'targetClientIds': [self.owner], 'version': 1,
                    'params': {'hostId': 'local', 'conversationId': self.thread_id, 'following': enabled}})

    def snapshot(self, thread_id):
        with self.bounded(16):
            return self._snapshot(thread_id)

    def _snapshot(self, thread_id):
        self.thread_id = str(uuid.UUID(thread_id))
        reply = self.request('thread-owner-discovery', {'hostId': 'local', 'conversationId': self.thread_id}, 1)
        self.owner = reply.get('handledByClientId')
        if not isinstance(self.owner, str):
            raise AppError('缺少 App 任务所有者')
        self.follow(False)
        self.snapshot_value = None
        self.follow(True)
        deadline = self.deadline()
        while self.snapshot_value is None:
            self._read(deadline)
        state = self.snapshot_value
        if not isinstance(state, dict) or state.get('id') != self.thread_id or state.get('hostId') != 'local':
            raise AppError('App 返回了不匹配的任务状态')
        if self.archived:
            raise AppError('所选聊天已经归档')
        state = local_persistence(state, self.home)
        if state.get('ephemeral') is None and self.metadata_reader is not None:
            metadata = self.metadata_reader(self.thread_id, self.deadline() - time.monotonic())
            if not isinstance(metadata, dict) or metadata.get('id') != self.thread_id:
                raise AppError('持久性查询返回了另一聊天，未接管任务')
            if isinstance(metadata.get('ephemeral'), bool):
                state = dict(state, ephemeral=metadata['ephemeral'], persistenceSource='official-metadata')
        return state

    def _drain_pending(self):
        """Consume already-arrived events before dispatch, never apply patches.

        This closes the queued-event window, not the inherent non-atomic gap
        between the App's state check and its start-turn operation. A busy or
        partial stream fails closed rather than allowing an unbounded drain.
        """
        deadline = self.deadline()
        for _ in range(128):
            if not select.select([self.sock], [], [], 0)[0]:
                return
            self._read(deadline)
        raise AppError('App 状态仍在变化；取消本次续跑')

    def mutate(self, thread_id, method, params, version, validate, dispatch_guard=None):
        with self.bounded(24):
            return self._mutate(thread_id, method, params, version, validate, dispatch_guard)

    def _mutate(self, thread_id, method, params, version, validate, dispatch_guard=None):
        """Fresh snapshot plus queued-event check; no uncertain mutation retries."""
        check_version(self.app_path)  # Validate the target app, not an exact version.
        try:
            state = self.snapshot(thread_id)
            if not validate(state):
                raise NotDispatched('操作前任务状态已改变；取消本次操作')
            self.dispatch_turn_id = latest_turn(state).get('turnId')
            generation = self.state_generation
            self._drain_pending()
        except (ConnectionUnavailable, TimeoutError, OSError) as exc:
            raise NotDispatched('操作前连接不可用，未发送请求') from exc
        except AppError as exc:
            raise NotDispatched(str(exc)) from exc
        if generation != self.state_generation or self.archived:
            raise NotDispatched('操作前已收到新的任务状态；取消本次操作')
        if dispatch_guard is not None and not dispatch_guard():
            raise NotDispatched('监控已取消；取消本次操作')
        return self.request(method, params, version, self.owner)

    def interrupt(self, thread_id, turn_id, validate, dispatch_guard=None):
        reply = self.mutate(thread_id, 'thread-follower-interrupt-turn',
            {'conversationId': thread_id, 'mode': 'system', 'expectedTurnId': turn_id},
            4, validate, dispatch_guard)
        result = reply.get('result', {})
        if result.get('ok') is not True or result.get('interruptedTurnId') != turn_id:
            raise AppError('中断没有得到匹配轮次的确认；不会自动重试')
        return turn_id

    def resume(self, thread_id, message_id, validate, dispatch_guard=None):
        request = {'threadId': thread_id, 'clientUserMessageId': message_id,
                   'input': [{'type': 'text', 'text': getattr(self, 'continuation', CONTINUATION), 'text_elements': []}]}
        reply = self.mutate(thread_id, 'thread-follower-start-turn',
            {'conversationId': thread_id, 'turnStart': {'request': request}},
            2, validate, dispatch_guard)
        try:
            turn_id = reply['result']['result']['turn']['id']
            if not isinstance(turn_id, str) or not turn_id or turn_id == self.dispatch_turn_id:
                raise KeyError('id')
        except (KeyError, TypeError) as exc:
            raise AppError('续跑结果不确定；请在 App 检查，不会自动重发') from exc
        return turn_id
