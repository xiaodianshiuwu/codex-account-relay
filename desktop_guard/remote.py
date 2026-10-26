"""Read-only SSH view of the existing app-server daemon. Never starts turns."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import struct
import subprocess
import time
from .desktop import ConnectionUnavailable

from .rpc import ReadOnlyServer

MAX_MESSAGE = 32 * 1024 * 1024


def daemon_version(initialization):
    value = initialization.get('userAgent', '') if isinstance(initialization, dict) else ''
    match = re.match(r'^codex-tui/(\S+)', value)
    return match.group(1) if match else None


def connection_key(host):
    return hashlib.sha256(json.dumps({k: host.get(k) for k in
        ('id', 'target', 'alias', 'port', 'identity')}, sort_keys=True).encode()).hexdigest()


def hosts(home):
    """Read only desktop connection declarations, never authentication material."""
    path = Path(home) / '.codex-global-state.json'
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or not isinstance(data.get('codex-managed-remote-connections', []), list):
        raise ValueError('桌面 SSH 主机配置格式不兼容')
    result = []
    seen = set()
    for item in data.get('codex-managed-remote-connections', []):
        if not isinstance(item, dict):
            continue
        host_id = item.get('hostId')
        if (not isinstance(host_id, str) or not host_id.startswith('remote-ssh-') or
                host_id in seen or len(host_id) > 512):
            continue
        alias = item.get('alias')
        hostname = item.get('hostname')
        target = alias.strip() if isinstance(alias, str) and alias.strip() else hostname
        # SSH's destination is one argv element; still refuse option-like/control values.
        if (not isinstance(target, str) or not target or target.startswith('-') or
                re.search(r'[\s\x00-\x1f\x7f]', target)):
            continue
        port, identity = item.get('sshPort'), item.get('identity')
        if port is not None and (not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535):
            continue
        if identity is not None and (not isinstance(identity, str) or '\x00' in identity):
            continue
        result.append({'id': host_id, 'name': item.get('displayName') or target,
                       'target': target, 'alias': bool(alias), 'port': port, 'identity': identity})
        seen.add(host_id)
    return result


def selected_host(home, host_id):
    for host in hosts(home):
        if host['id'] == host_id:
            return host
    raise RuntimeError('所选 SSH 主机已不在桌面连接配置中，请刷新列表')


def ssh_command(host):
    # Match the desktop's login-shell/CODEX_HOME semantics. No installation,
    # daemon restart, symlink changes, agent forwarding or copied credentials.
    payload = 'PATH="${CODEX_INSTALL_DIR:-$HOME/.local/bin}:$PATH"; export PATH; exec codex app-server proxy'
    inner = 'exec /bin/sh -c "$CODEX_REMOTE_PAYLOAD"'
    profile = 'CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"; export CODEX_HOME; ' + inner
    script = ('if [ -z "$SHELL" ] || [ ! -x "$SHELL" ]; then exit 127; fi; '
              'CODEX_REMOTE_PAYLOAD="$1"; export CODEX_REMOTE_PAYLOAD; '
              'case "${SHELL##*/}" in '
              'csh|tcsh) exec "$SHELL" -i -c ' + shlex.quote(
                  'set loginsh=1; if ( -r /etc/csh.login ) source /etc/csh.login; '
                  'if ( -r ~/.login ) source ~/.login; ' + inner) + ' ;; '
              'nu) exec "$SHELL" -l -i -c ' + shlex.quote('exec /bin/sh -c $env.CODEX_REMOTE_PAYLOAD') + ' ;; '
              'fish|xonsh) exec "$SHELL" -l -i -c ' + shlex.quote(inner) + ' ;; '
              '*) exec "$SHELL" -l -i -c ' + shlex.quote(profile) + ' ;; esac')
    args = ['/usr/bin/ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ConnectTimeout=8', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2']
    if not host['alias']:
        if host['identity']:
            args += ['-i', str(Path(host['identity']).expanduser())]
        if host['port']:
            args += ['-p', str(host['port'])]
    return args + ['--', host['target'], 'sh -c ' + shlex.quote(script) + ' sh ' + shlex.quote(payload)]


class RemoteReadOnlyServer(ReadOnlyServer):
    """Bounded RFC 6455 client over SSH's stdio, using the daemon's /rpc endpoint."""
    def __init__(self, host, timeout=15):
        super().__init__('', '', timeout)
        self.host = host
        self.initialization = None

    def __enter__(self):
        self.deadline()
        self.process = subprocess.Popen(ssh_command(self.host), stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        try:
            self._handshake()
            self.initialization = self._request('initialize', {
                'clientInfo': {'name': 'personal_desktop_guard_view', 'version': '2.0.0'},
                'capabilities': {'experimentalApi': True}})
            self._send({'method': 'initialized'})
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def _receive(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not self.selector.select(remaining):
            raise TimeoutError('SSH 只读连接超时，请先在 Codex 中连接该主机')
        chunk = os.read(self.process.stdout.fileno(), 65536)
        if not chunk:
            raise ConnectionUnavailable('SSH 连接已断开，请检查桌面连接和 SSH 登录')
        self.buffer += chunk
        if len(self.buffer) > MAX_MESSAGE + 65536:
            raise RuntimeError('SSH 查询响应过大')

    def _exact(self, size, deadline):
        while len(self.buffer) < size:
            self._receive(deadline)
        data, self.buffer = self.buffer[:size], self.buffer[size:]
        return data

    def _handshake(self):
        key = base64.b64encode(os.urandom(16)).decode()
        request = ('GET /rpc HTTP/1.1\r\nHost: codex-app-server\r\nUpgrade: websocket\r\n'
                   'Connection: Upgrade\r\nSec-WebSocket-Key: ' + key +
                   '\r\nSec-WebSocket-Version: 13\r\n\r\n')
        self.process.stdin.write(request.encode('ascii'))
        self.process.stdin.flush()
        deadline = self.deadline()
        while b'\r\n\r\n' not in self.buffer:
            self._receive(deadline)
            if len(self.buffer) > 32768:
                raise RuntimeError('SSH WebSocket 握手格式不受支持')
        header, self.buffer = self.buffer.split(b'\r\n\r\n', 1)
        # Login shells sometimes print a banner. Only an authenticated SSH peer
        # can supply these bytes; require a complete valid WebSocket handshake.
        start = header.find(b'HTTP/1.1 ')
        lines = header[start:].decode('ascii').split('\r\n') if start >= 0 else []
        values = {key.lower(): value.strip() for key, value in
                  (line.split(':', 1) for line in lines[1:] if ':' in line)}
        accept = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        if (not lines or len(lines[0].split()) < 2 or lines[0].split()[1] != '101' or
                values.get('sec-websocket-accept', '') != accept or
                values.get('upgrade', '').lower() != 'websocket' or
                'upgrade' not in [v.strip().lower() for v in values.get('connection', '').split(',')]):
            raise RuntimeError('SSH WebSocket 握手校验失败')

    def _frame(self, payload, opcode=1):
        if len(payload) > MAX_MESSAGE:
            raise RuntimeError('SSH 请求过大')
        mask = os.urandom(4)
        size = len(payload)
        header = bytes([0x80 | opcode])
        header += (bytes([0x80 | size]) if size < 126 else
                   bytes([0x80 | 126]) + struct.pack('!H', size) if size <= 65535 else
                   bytes([0x80 | 127]) + struct.pack('!Q', size))
        encoded = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.process.stdin.write(header + mask + encoded)
        self.process.stdin.flush()

    def _send(self, data):
        self._frame(json.dumps(data, ensure_ascii=False).encode())

    def _line(self, deadline):
        parts, total, started = [], 0, False
        while True:
            first, second = self._exact(2, deadline)
            final, opcode, size = bool(first & 0x80), first & 15, second & 127
            if first & 0x70 or second & 0x80:
                raise RuntimeError('SSH WebSocket 帧格式不受支持')
            if size == 126:
                size = struct.unpack('!H', self._exact(2, deadline))[0]
            elif size == 127:
                size = struct.unpack('!Q', self._exact(8, deadline))[0]
            if size > MAX_MESSAGE or total + size > MAX_MESSAGE:
                raise RuntimeError('SSH 查询消息过大')
            if opcode >= 8 and (not final or size > 125):
                raise RuntimeError('SSH WebSocket 控制帧格式不受支持')
            payload = self._exact(size, deadline)
            if opcode == 8:
                raise ConnectionUnavailable('SSH 服务器关闭了连接')
            if opcode == 9:
                self._frame(payload, 10)
                continue
            if opcode == 10:
                continue
            if (opcode == 1 and started) or (opcode == 0 and not started) or opcode not in (0, 1):
                raise RuntimeError('SSH WebSocket 消息格式不受支持')
            started = True
            parts.append(payload)
            total += size
            if final:
                result = json.loads(b''.join(parts).decode('utf-8'))
                if not isinstance(result, dict):
                    raise RuntimeError('SSH 返回了非对象响应')
                return result
    METHODS = ReadOnlyServer.METHODS | {'thread/turns/list', 'thread/goal/get', 'thread/queue/list'}
