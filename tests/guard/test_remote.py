import base64
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from codex_relay import guard_cli as guard
from desktop_guard.remote import hosts, selected_host, ssh_command, RemoteReadOnlyServer, MAX_MESSAGE
from desktop_guard.remote_control import RemoteController
from desktop_guard.desktop import AppError, NotDispatched, ConnectionUnavailable
from desktop_guard.policy import task, identity
from desktop_guard.engine import Engine
from desktop_guard.store import Store, statuses
from .test_guard import state, usage, FakeDesktop

THREAD = '11111111-1111-4111-8111-111111111111'
HOST = 'remote-ssh-discovered:example'
DESCRIPTOR = {'id': HOST, 'name': 'example', 'target': 'example', 'alias': True,
              'port': None, 'identity': None}


class RemoteViewTests(unittest.TestCase):
    def test_only_valid_declared_ssh_hosts_are_discovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = {'codex-managed-remote-connections': [
                {'hostId': HOST, 'displayName': 'My SSH', 'alias': 'example'},
                {'hostId': HOST, 'alias': 'duplicate'},
                {'hostId': 'durable', 'alias': 'cloud'},
                {'hostId': 'remote-ssh-invalid', 'alias': '-oProxyCommand=bad'},
                {'hostId': 'remote-ssh-manual:abc', 'hostname': 'user@example', 'sshPort': 2222,
                 'identity': '/synthetic/space key'}]}
            (Path(tmp) / '.codex-global-state.json').write_text(json.dumps(data))
            result = hosts(tmp)
            self.assertEqual([h['id'] for h in result], [HOST, 'remote-ssh-manual:abc'])
            self.assertEqual(selected_host(tmp, HOST)['target'], 'example')
            with self.assertRaises(RuntimeError):
                selected_host(tmp, 'removed-host')

    def test_malformed_inventory_does_not_masquerade_as_no_ssh_hosts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '.codex-global-state.json'
            for data in ([], {'codex-managed-remote-connections': None}):
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    hosts(tmp)

    def test_ssh_uses_existing_proxy_without_install_or_daemon_restart(self):
        command = ssh_command(DESCRIPTOR)
        self.assertIn('BatchMode=yes', command)
        self.assertIn('StrictHostKeyChecking=yes', command)
        self.assertEqual(command[-2], 'example')
        self.assertIn('app-server proxy', command[-1])
        for dangerous in ('turn/start', 'turn/interrupt', 'daemon start', 'ln -', 'curl ', 'auth.json'):
            self.assertNotIn(dangerous, command[-1])
        direct = dict(DESCRIPTOR, alias=False, target='user@example', port=2222, identity='/synthetic/space key')
        command = ssh_command(direct)
        self.assertEqual(command[command.index('-i') + 1], '/synthetic/space key')
        self.assertEqual(command[command.index('-p') + 1], '2222')

    def test_remote_rpc_refuses_all_mutation_methods_before_transport(self):
        reader = RemoteReadOnlyServer(DESCRIPTOR)
        with patch.object(reader, '_request') as request:
            for method in ('turn/start', 'turn/interrupt', 'thread/resume', 'account/login/start'):
                with self.assertRaises(ValueError):
                    reader.query(method, {'threadId': THREAD})
            request.assert_not_called()

    def test_remote_controller_is_bound_to_declared_host(self):
        args = SimpleNamespace(host=HOST, home='/synthetic', connection_key='expected-key')
        with patch.object(guard, 'selected_host', return_value=DESCRIPTOR), patch.object(guard, 'RemoteController') as controller:
            guard.desktop(args)
            controller.assert_called_once_with(DESCRIPTOR, expected_key='expected-key')

    def test_remote_check_reads_same_host_and_enables_eligible_monitoring(self):
        args = SimpleNamespace(host=HOST, app=Path('/synthetic'))
        current = state();current.update(hostId=HOST, canAcceptDirectInput=True)
        with patch.object(guard, 'check_version'), patch.object(guard, 'desktop') as server:
            server.return_value.__enter__.return_value.snapshot.return_value = current
            result = guard.check(args, THREAD)
        self.assertEqual(result['hostId'], HOST)
        self.assertEqual(result['kind'], 'running')
        self.assertTrue(result['canMonitor'])

    def test_remote_check_rejects_wrong_thread_response(self):
        args = SimpleNamespace(host=HOST, app=Path('/synthetic'))
        with patch.object(guard, 'check_version'), patch.object(guard, 'desktop') as server:
            server.return_value.__enter__.return_value.snapshot.return_value = {'id': 'wrong'}
            with self.assertRaises(RuntimeError):
                guard.check(args, THREAD)

    def test_disconnected_remote_has_no_quota_and_does_not_query_local_account(self):
        args = SimpleNamespace(home=Path('/synthetic'), app=Path('/synthetic'))
        with patch.object(guard, 'RemoteReadOnlyServer') as remote, patch.object(guard, 'ReadOnlyServer') as local:
            remote.return_value.__enter__.side_effect = TimeoutError()
            result = guard.host_snapshot(args, DESCRIPTOR)
            local.assert_not_called()
        self.assertFalse(result['connected'])
        self.assertFalse(result['quotaKnown'])
        self.assertEqual(result['windows'], [])

    def test_remote_quota_failure_still_preserves_remote_chats(self):
        args = SimpleNamespace(home=Path('/synthetic'), app=Path('/synthetic'))
        with patch.object(guard, 'RemoteReadOnlyServer') as remote:
            remote.return_value.__enter__.return_value.initialization = {'userAgent': 'codex-tui/0.162.0'}
            remote.return_value.__enter__.return_value.query.side_effect = [
                {'data': [{'id': THREAD, 'name': 'Remote task', 'status': {'type': 'active'}}]}, RuntimeError()]
            result = guard.host_snapshot(args, DESCRIPTOR)
        self.assertTrue(result['connected'])
        self.assertFalse(result['quotaKnown'])
        self.assertEqual(result['threads'][0]['hostId'], HOST)
        self.assertTrue(result['threads'][0]['canControl'])

    def test_remote_record_cannot_be_loaded_as_local_monitor_with_same_uuid(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = {'threadId': THREAD, 'hostId': HOST, 'attempts': {}}
            (Path(tmp) / (THREAD + '.json')).write_text(json.dumps(record))
            self.assertEqual(statuses(tmp), [])
            with self.assertRaises(RuntimeError):
                Store(tmp, THREAD)


class WebSocketTests(unittest.TestCase):
    def reader(self, data=b''):
        reader = RemoteReadOnlyServer(DESCRIPTOR)
        reader.buffer = data
        reader.process = SimpleNamespace(stdin=io.BytesIO())
        return reader

    def test_inbound_fragmented_message_with_interleaved_ping(self):
        reader = self.reader(b'\x01\x06{"id":' + b'\x89\x01p' + b'\x80\x021}')
        self.assertEqual(reader._line(time.monotonic() + 1), {'id': 1})
        pong = reader.process.stdin.getvalue()
        self.assertEqual(pong[0], 0x8a)
        self.assertTrue(pong[1] & 0x80)  # Client replies are masked.

    def test_oversize_masked_and_unexpected_continuation_frames_are_rejected(self):
        for data in (b'\x81\x7f' + struct.pack('!Q', MAX_MESSAGE + 1), b'\x81\x80', b'\x80\x00'):
            reader = self.reader(data)
            with self.assertRaises(RuntimeError):
                reader._line(time.monotonic() + 1)

    def test_outbound_frame_is_masked_and_retains_utf8_rpc_json(self):
        reader = self.reader()
        reader._send({'method': 'thread/list', 'text': '中文'})
        frame = reader.process.stdin.getvalue()
        self.assertEqual(frame[0], 0x81)
        length = frame[1] & 127
        mask, payload = frame[2:6], frame[6:]
        self.assertEqual(length, len(payload))
        decoded = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.assertEqual(json.loads(decoded)['text'], '中文')

    def test_handshake_requires_valid_accept_header_and_retains_first_frame(self):
        seed = b'0' * 16
        key = base64.b64encode(seed).decode()
        expected = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        for value in (expected, 'incorrect'):
            reader = self.reader(('banner\nHTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\n'
                                  'Upgrade: websocket\r\nSec-WebSocket-Accept: ' + value + '\r\n\r\n').encode() + b'\x81\x00')
            with patch('desktop_guard.remote.os.urandom', return_value=seed):
                if value == expected:
                    reader._handshake()
                    self.assertEqual(reader.buffer, b'\x81\x00')
                else:
                    with self.assertRaises(RuntimeError):
                        reader._handshake()
