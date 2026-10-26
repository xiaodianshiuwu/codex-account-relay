"""Regressions for actual Desktop metadata omissions and delayed queries."""
import copy
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from codex_relay import guard_cli as guard
from desktop_guard.desktop import Desktop, local_persistence, AppError, NotDispatched, ConnectionUnavailable
from desktop_guard.policy import task
from desktop_guard.rpc import ReadOnlyServer
from desktop_guard.remote_control import RemoteController
from .test_guard import state, THREAD
from .test_remote import DESCRIPTOR, HOST


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.rollout = self.home / 'sessions/2026/10/09' / ('rollout-fixture-' + THREAD + '.jsonl')
        self.rollout.parent.mkdir(parents=True)
        self.rollout.write_text('Do not read conversation contents.\n')
        self.current = state()
        self.current.pop('ephemeral')
        self.current.update(sessionId=THREAD, rolloutPath=str(self.rollout))

    def test_live_ipc_shape_missing_ephemeral_is_verified_without_reading_history(self):
        with patch.object(Path, 'read_text', side_effect=AssertionError('history read')):
            verified = local_persistence(self.current, self.home)
        self.assertEqual(task(verified).kind, 'running')
        self.assertEqual(verified['persistenceSource'], 'registered-rollout')
        self.assertNotIn('ephemeral', self.current)

    def test_explicit_temporary_chat_is_not_overridden_by_rollout(self):
        self.current['ephemeral'] = True
        self.assertEqual(task(local_persistence(self.current, self.home)).kind, 'blocked')

    def test_invalid_or_missing_rollout_never_establishes_persistence(self):
        outside = self.home / ('outside-' + THREAD + '.jsonl')
        outside.write_text('fixture')
        alias = self.rollout.parent / ('alias-' + THREAD + '.jsonl')
        alias.symlink_to(self.rollout)
        for path in (None, '/missing', str(outside), str(alias), str(self.rollout.parent), 'sessions/relative'):
            with self.subTest(path=path):
                data = dict(self.current, rolloutPath=path)
                self.assertEqual(task(local_persistence(data, self.home)).kind, 'blocked')
        self.current['sessionId'] = '22222222-2222-4222-8222-222222222222'
        self.assertEqual(task(local_persistence(self.current, self.home)).kind, 'blocked')

    def test_persistence_and_host_failures_have_distinct_reasons(self):
        verified = local_persistence(self.current, self.home)
        self.assertIn('主机', task(verified, HOST).reason)
        self.assertIn('持久性', task(local_persistence(dict(self.current, rolloutPath=None), self.home)).reason)
        self.assertIn('临时聊天', task(dict(verified, ephemeral=True)).reason)

    def snapshot_with_metadata(self, metadata):
        d = Desktop(self.home, metadata_reader=lambda tid, budget: metadata)
        s = dict(self.current, rolloutPath=None)
        def receive(deadline): d.snapshot_value = copy.deepcopy(s)
        with (patch.object(d, 'request', return_value={'handledByClientId': 'owner'}),
              patch.object(d, 'follow'), patch.object(d, '_read', side_effect=receive)):
            return d.snapshot(THREAD)

    def test_official_metadata_can_verify_persistence_without_a_rollout(self):
        result = self.snapshot_with_metadata({'id': THREAD, 'ephemeral': False})
        self.assertEqual(task(result).kind, 'running')
        self.assertEqual(result['persistenceSource'], 'official-metadata')

    def test_metadata_for_another_uuid_is_rejected(self):
        with self.assertRaises(AppError): self.snapshot_with_metadata({'id': 'wrong', 'ephemeral': False})

    def test_unknown_and_temporary_official_metadata_remain_blocked(self):
        for value in (None, True, 'false'):
            self.assertEqual(task(self.snapshot_with_metadata({'id': THREAD, 'ephemeral': value})).kind, 'blocked')

    def test_cleanup_after_expired_deadline_does_not_hide_original_error(self):
        d = Desktop(self.home)
        d.sock = socket.socket(socket.AF_UNIX)
        d.owner, d.thread_id = 'owner', THREAD
        with d.bounded(0): d.close()
        self.assertIsNone(d.sock)


class QueryDeadlineTests(unittest.TestCase):
    def test_total_deadline_spans_multiple_real_rpc_responses(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / 'fixture'
            binary.write_text('#!' + sys.executable + '\n' + '''import sys,json,time
for line in sys.stdin:
    message=json.loads(line)
    if 'id' not in message: continue
    if message['method'] != 'initialize': time.sleep(0.17)
    print(json.dumps({'id':message['id'],'result':{'fixture':True}}),flush=True)
''')
            binary.chmod(0o700)
            reader = ReadOnlyServer(binary, temporary, timeout=2)
            start = time.monotonic()
            with self.assertRaises(TimeoutError):
                with reader.bounded(0.43), reader as rpc:
                    rpc.query('thread/read'); rpc.query('thread/read'); rpc.query('thread/read')
            self.assertLess(time.monotonic() - start, 1.5)
            self.assertIsNone(reader.process)
            self.assertIsNone(reader.selector)
            with reader.bounded(0.2):
                outer = reader.operation_deadline
                with reader.bounded(5): self.assertEqual(reader.operation_deadline, outer)
            self.assertIsNone(reader.operation_deadline)

    def test_local_and_remote_preflight_failures_are_not_dispatched(self):
        for controller in (Desktop('/synthetic'), RemoteController(DESCRIPTOR)):
            for error in (TimeoutError('slow'), ConnectionUnavailable('offline'), AppError('invalid state')):
                with self.subTest(controller=type(controller).__name__, error=type(error).__name__):
                    with (patch.object(controller, 'snapshot', side_effect=error),
                          patch.object(controller, 'request' if isinstance(controller, Desktop) else '_request') as request,
                          patch('desktop_guard.desktop.check_version')):
                        with self.assertRaises(NotDispatched): controller.interrupt(THREAD, 'turn1', lambda s: True)
                        request.assert_not_called()

    def test_old_turn_id_is_not_accepted_as_a_resume_receipt(self):
        d = Desktop('/synthetic'); d.dispatch_turn_id = 'turn1'
        with patch.object(d, 'mutate', return_value={'result': {'result': {'turn': {'id': 'turn1'}}}}):
            with self.assertRaises(AppError): d.resume(THREAD, 'message-id', lambda s: True)


class EnrollmentTests(unittest.TestCase):
    def test_old_pause_cannot_bypass_blocked_finished_or_changed_turn(self):
        record = {'phase': 'waiting', 'paused': {'turnId': 'turn1'}}
        for kind in ('blocked', 'finished', 'unknown', 'error'):
            self.assertFalse(guard.can_enroll({'kind': kind, 'canMonitor': False, 'turnId': 'turn1'}, record))
        self.assertFalse(guard.can_enroll({'kind': 'interrupted', 'turnId': 'different'}, record))
        self.assertFalse(guard.can_enroll({'kind': 'blocked', 'canMonitor': True}, record))

    def test_matching_retained_pause_may_be_rechecked_by_engine(self):
        record = {'phase': 'waiting', 'paused': {'turnId': 'turn1'}}
        self.assertTrue(guard.can_enroll({'kind': 'interrupted', 'turnId': 'turn1'}, record))
        self.assertTrue(guard.can_enroll({'kind': 'connecting', 'turnId': ''}, record))
        self.assertFalse(guard.can_enroll({'kind': 'interrupted', 'turnId': 'turn1'}, dict(record, phase='finished')))


class RemoteTransitionTests(unittest.TestCase):
    def test_nested_thread_notification_invalidates_pending_validation(self):
        c = RemoteController(DESCRIPTOR); c.thread_id = THREAD
        c._observe({'method': 'thread/started', 'params': {'thread': {'id': THREAD}}})
        self.assertEqual(c.generation, 1)
        c._observe({'method': 'thread/started', 'params': {'thread': {'id': 'other'}}})
        self.assertEqual(c.generation, 1)

    def test_empty_turns_are_reported_without_auto_reopening(self):
        c = RemoteController(DESCRIPTOR, keep_alive=True)
        with (patch.object(c, 'query', side_effect=[{'thread': {'id': THREAD, 'status': {'type': 'notLoaded'}}}, {'data': []}]),
              patch.object(c, '_request') as mutation):
            with self.assertRaisesRegex(AppError, '没有轮次'): c.snapshot(THREAD)
            mutation.assert_not_called()

    def test_changed_subscription_settings_wait_for_sync(self):
        c = RemoteController(DESCRIPTOR, keep_alive=True)
        summary = {'id': THREAD, 'sessionId': THREAD, 'status': {'type': 'active'}, 'model': 'model', 'cwd': '/original'}
        loaded = {'thread': summary, 'model': 'changed', 'cwd': '/original'}
        with patch.object(c, 'query', return_value={'thread': summary}), patch.object(c, '_request', return_value=loaded):
            with self.assertRaises(ConnectionUnavailable): c.snapshot(THREAD)


if __name__ == '__main__': unittest.main()
