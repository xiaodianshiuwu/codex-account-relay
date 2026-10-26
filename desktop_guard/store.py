"""Crash-safe local ledger and one monitor process per exact thread UUID."""
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time
import uuid
from contextlib import contextmanager


def host_directory(directory, host_id='local'):
    root = Path(directory)
    if host_id == 'local':
        return root
    if not isinstance(host_id, str) or not host_id.startswith('remote-ssh-') or len(host_id) > 512:
        raise ValueError('不支持的监控主机')
    return root / 'hosts' / hashlib.sha256(host_id.encode()).hexdigest()


def validate_record(data, thread_id, host_id):
    def invalid():
        raise RuntimeError('本地监控记录格式不兼容；请保留记录并检查，不会自动重试')
    if (not isinstance(data, dict) or data.get('threadId') != thread_id or
            data.get('hostId', 'local') != host_id or not isinstance(data.get('attempts'), dict)):
        invalid()
    if (not isinstance(data.get('enabled'), bool) or not isinstance(data.get('phase'), str) or
            type(data.get('resumeCount')) is not int or data['resumeCount'] < 0):
        invalid()
    for key, attempt in data['attempts'].items():
        if (not isinstance(key, str) or not isinstance(attempt, dict) or
                attempt.get('status') not in ('intent', 'confirmed', 'not_sent')):
            invalid()
        if attempt['status'] == 'confirmed' and (not isinstance(attempt.get('resultId'), str) or not attempt['resultId']):
            invalid()
    paused = data.get('paused')
    if paused is not None:
        if (not isinstance(paused, dict) or paused.get('cause') not in ('threshold', 'quota_error') or
                any(not isinstance(paused.get(k), str) or not paused[k] for k in ('turnId', 'identity')) or
                type(paused.get('at')) not in (int, float) or not math.isfinite(paused['at'])):
            invalid()
    for key in ('graceUntil', 'nextQuotaCheck'):
        if key in data and (type(data[key]) not in (int, float) or not math.isfinite(data[key])):
            invalid()
    return data


class Store:
    def __init__(self, directory, thread_id, host_id='local'):
        self.host_id = host_id
        self.directory = host_directory(directory, host_id)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        self.path = self.directory / (thread_id + '.json')
        self.cancel_path = self.directory / (thread_id + '.cancel')
        self.lock_path = self.directory / (thread_id + '.lock')
        self.control_path = self.directory / (thread_id + '.control')
        self.thread_id = thread_id
        self.file = None
        self.cancel_requested = False
        self.data = self.read()

    def read(self):
        if not self.path.exists():
            return {'threadId': self.thread_id, 'hostId': self.host_id, 'phase': 'watching', 'resumeCount': 0,
                    'attempts': {}, 'enabled': False}
        data = json.loads(self.path.read_text())
        return validate_record(data, self.thread_id, self.host_id)

    def save(self, **changes):
        self.data.update(changes)
        self.data['updatedAt'] = time.time()
        fd, path = tempfile.mkstemp(prefix=self.thread_id + '.', dir=self.directory)
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(path, self.path)
            # Persist the rename too, where the filesystem supports directory fsync.
            fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def acquire(self):
        self.file = self.lock_path.open('a+')
        os.chmod(self.lock_path, 0o600)
        try:
            fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.file.close()
            self.file = None
            raise RuntimeError('该聊天已有监控进程；不会重复启动') from exc
        # Another process may have updated the ledger between construction and locking.
        try:
            self.data = self.read()
        except Exception:
            self.close()
            raise
        self.file.seek(0)
        self.file.truncate()
        self.file.write(str(os.getpid()))
        self.file.flush()

    def close(self):
        if self.file:
            self.file.close()
            self.file = None

    def live(self):
        return self.data.get('enabled', False) and not self.cancel_requested and not self.cancel_path.exists()

    @contextmanager
    def control(self):
        with self.control_path.open('a+') as f:
            os.chmod(self.control_path, 0o600)
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            yield

    def begin(self, requested_at_ns=None):
        if self.data.get('phase') == 'uncertain' or any(
                a.get('status') == 'intent' for a in self.data['attempts'].values()):
            raise RuntimeError('存在结果不确定的操作，请先检查原聊天；禁止自动重试')
        with self.control():
            if (requested_at_ns is not None and self.cancel_path.exists() and
                    self.cancel_path.stat().st_mtime_ns > requested_at_ns):
                raise RuntimeError('启动期间已取消监控，未执行任何自动操作')
            self.cancel_path.unlink(missing_ok=True)
            if self.data.get('phase') in ('stopped', 'finished', 'attention'):
                self.data.pop('paused', None)
                self.data.pop('lastResumedTurn', None)
                self.data.pop('lastContinuationTurn', None)
                self.data.pop('graceUntil', None)
                self.data.pop('nextQuotaCheck', None)
                self.data.pop('remoteBinding', None)
                self.data['phase'] = 'watching'
            self.save(enabled=True, pid=os.getpid())

    def claim(self, action, turn_id, message_id):
        key = action + ':' + turn_id
        previous = self.data['attempts'].get(key, {})
        if not self.live() or (previous and previous.get('status') != 'not_sent'):
            raise RuntimeError('已取消监控，或该轮次已有操作记录')
        self.data['attempts'][key] = {'status': 'intent', 'messageId': message_id, 'at': time.time(),
                                    'attemptCount': previous.get('attemptCount', 0) + 1}
        self.save()
        return key

    def acknowledge(self, key, result_id):
        self.data['attempts'][key].update(status='confirmed', resultId=result_id)
        self.save()

    def end(self, phase, reason):
        self.save(enabled=False, phase=phase, reason=reason)

    def cancel(self):
        with self.control():
            fd = os.open(self.cancel_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.close(fd)


def running(directory, thread_id, host_id='local'):
    """Use the process-held lock, not a stale PID or persisted enabled flag."""
    path = host_directory(directory, host_id) / (thread_id + '.lock')
    if not path.exists():
        return False
    with path.open('r') as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            return True


def statuses(directory):
    result = []
    root = Path(directory)
    for path in sorted(list(root.glob('*.json')) + list(root.glob('hosts/*/*.json'))):
        try:
            data = json.loads(path.read_text())
            thread_id = data['threadId']
            if str(uuid.UUID(thread_id)) != path.stem:
                continue
            host_id = data.get('hostId', 'local')
            validate_record(data, thread_id, host_id)
            if path.parent != host_directory(root, host_id):
                continue
            data['hostId'] = host_id
            alive = running(directory, thread_id, host_id)
            cancelled = (path.parent / (thread_id + '.cancel')).exists()
            data['processAlive'] = alive
            data['monitoring'] = alive and data.get('enabled', False) and not cancelled
            if cancelled:
                data['displayPhase'] = 'stopping' if alive else 'stopped'
            elif data.get('enabled') and not alive:
                data['displayPhase'] = 'offline'
            else:
                data['displayPhase'] = data.get('phase', 'unknown')
            result.append(data)
        except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError):
            continue
    return result
