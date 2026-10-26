import copy
import json
import os
from pathlib import Path
import plistlib
import socket
import struct
import tempfile
import threading
import unittest

from desktop_guard.desktop import Desktop, AppError, NotDispatched
from desktop_guard.engine import Engine
from desktop_guard.policy import quota, task, identity
from desktop_guard.store import Store, statuses

THREAD = '11111111-1111-4111-8111-111111111111'


def state(status='inProgress', tid='turn1', error=None):
    return {'id': THREAD, 'hostId': 'local', 'ephemeral': False, 'resumeState': 'resumed',
            'requests': [], 'queuedFollowUps': [], 'currentPermissions': {'sandbox': 'workspace-write'},
            'latestModel': 'original-model', 'cwd': '/synthetic/project',
            'threadRuntimeStatus': {'type': 'active' if status == 'inProgress' else 'idle', 'activeFlags': []},
            'turns': [{'turnId': tid, 'status': status, 'error': error, 'params': {'input': 'synthetic'}}]}


def usage(used=10, weekly=20, primary_reset=1100, secondary_reset=2000):
    return {'rateLimitsByLimitId': {'codex': {'primary': {'usedPercent': used, 'resetsAt': primary_reset,
                                                          'windowDurationMins': 300},
                                            'secondary': {'usedPercent': weekly, 'resetsAt': secondary_reset,
                                                          'windowDurationMins': 10080}}}}


class FakeDesktop:
    def __init__(self, env):
        self.env = env
    def __enter__(self):
        return self
    def __exit__(self, *unused):
        pass
    def snapshot(self, tid):
        return copy.deepcopy(self.env['state'])
    def interrupt(self, tid, turn, validate, guard):
        if self.env.get('before_interrupt'):
            self.env['before_interrupt']()
        if not validate(self.snapshot(tid)) or not guard():
            raise NotDispatched('state changed')
        self.env['interrupts'] += 1
        self.env['state'] = state('interrupted', turn)
        if self.env.get('interrupt_timeout'):
            raise TimeoutError()
        return turn
    def resume(self, tid, mid, validate, guard):
        if self.env.get('before_resume'):
            self.env['before_resume']()
        if not validate(self.snapshot(tid)) or not guard():
            raise NotDispatched('state changed')
        self.env['resumes'] += 1
        self.env['state'] = state('inProgress', 'turn2')
        if self.env.get('resume_timeout'):
            raise TimeoutError()
        return 'turn2'


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name, THREAD)
        self.store.acquire()
        self.addCleanup(self.store.close)
        self.store.begin()
        self.now = 1000
        self.env = {'state': state(), 'usage': usage(), 'interrupts': 0, 'resumes': 0}
        self.engine = Engine(self.store, lambda: FakeDesktop(self.env), lambda: self.env['usage'],
                             clock=lambda: self.now)

    def low(self, primary=95, weekly=20):
        self.env['usage'] = usage(primary, weekly)

    def restore(self):
        self.now = 1120
        self.env['usage'] = usage(primary_reset=3000, secondary_reset=4000)

    def test_exact_five_percent_then_reset_resumes_once_and_completion_stops(self):
        self.low()
        self.assertTrue(self.engine.step())
        self.assertEqual(self.env['interrupts'], 1)
        self.assertTrue(self.engine.step())
        self.assertEqual(self.env['resumes'], 0)
        self.restore()
        self.assertTrue(self.engine.step())
        self.assertEqual(self.env['resumes'], 1)
        self.assertTrue(self.engine.step())
        self.assertEqual(self.env['resumes'], 1)
        self.env['state'] = state('completed', 'turn2')
        self.assertFalse(self.engine.step())
        self.assertEqual(self.store.data['phase'], 'finished')

    def test_low_or_exhausted_weekly_window_never_interrupts_healthy_hours(self):
        for weekly in (95, 97, 100):
            self.low(primary=10, weekly=weekly)
            self.assertTrue(self.engine.step())
            self.assertEqual(self.env['interrupts'], 0)
            self.assertIsNone(self.store.data.get('paused'))

    def test_hourly_pause_and_recovery_ignore_weekly_threshold_and_reset(self):
        self.low(primary=95, weekly=100)
        self.assertTrue(self.engine.step())
        self.assertEqual(self.env['interrupts'], 1)
        self.assertTrue(self.engine.step())
        self.assertEqual(self.engine.next_quota_check, 1060)
        self.now = 1120
        self.env['usage'] = usage(used=90, weekly=100, primary_reset=3000, secondary_reset=9000)
        self.assertTrue(self.engine.step())
        self.assertEqual(self.env['resumes'], 1)
        self.assertTrue(self.engine.step())
        self.assertEqual(self.env['resumes'], 1)

    def test_early_hourly_recovery_is_detected_before_old_reset_estimate(self):
        self.env['usage'] = usage(95, primary_reset=5000)
        self.assertTrue(self.engine.step())
        self.assertTrue(self.engine.step())
        self.assertEqual(self.env['resumes'], 0)
        self.now = 1061
        self.env['usage'] = usage(90, primary_reset=5000)
        self.assertTrue(self.engine.step())
        self.assertEqual(self.env['resumes'], 1)
        self.assertLess(self.now, 5000)

    def test_generic_server_limit_does_not_proactively_interrupt_healthy_hours(self):
        self.env['usage']['rateLimitsByLimitId']['codex']['rateLimitReachedType'] = 'rate_limit_reached'
        self.assertTrue(self.engine.step())
        self.assertEqual(self.env['interrupts'], 0)

    def test_rejected_continuation_with_healthy_hours_never_loops_on_weekly_failure(self):
        self.env['usage'] = usage(used=10, weekly=100)
        self.env['state'] = state('failed', error={'codexErrorInfo':'UsageLimitExceeded'})
        self.assertTrue(self.engine.step())
        self.assertTrue(self.engine.step())
        self.assertEqual(self.env['resumes'], 1)
        # The server can enforce a real limit independently of our local policy.
        # Even without a reached flag in the quota response, its error is decisive.
        self.env['state'] = state('failed','turn2',{'codexErrorInfo':'UsageLimitExceeded'})
        self.assertFalse(self.engine.step())
        self.assertEqual(self.store.data['phase'], 'attention')
        self.assertFalse(self.engine.step())
        self.assertEqual(self.env['resumes'], 1)

    def test_no_interrupt_above_five(self):
        self.low(primary=94)
        self.engine.step()
        self.assertEqual(self.env['interrupts'], 0)

    def test_manual_interrupt_is_never_resumed(self):
        self.env['state'] = state('interrupted')
        self.assertFalse(self.engine.step())
        self.assertEqual(self.env['resumes'], 0)

    def test_approval_and_user_input_stop_monitor(self):
        self.env['state']['requests'] = [{'method': 'item/commandExecution/requestApproval'}]
        self.assertFalse(self.engine.step())
        self.assertEqual(self.store.data['phase'], 'attention')

    def test_queued_user_message_stops(self):
        self.env['state']['queuedFollowUps'] = [{'input': 'new scope'}]
        self.assertFalse(self.engine.step())

    def test_mutated_settings_during_wait_do_not_resume(self):
        self.low()
        self.engine.step()
        self.env['state']['latestModel'] = 'different-model'
        self.restore()
        self.assertFalse(self.engine.step())
        self.assertEqual(self.env['resumes'], 0)

    def test_cancel_during_final_dispatch_guard_prevents_send(self):
        self.low()
        self.engine.step()
        self.restore()
        self.env['before_resume'] = self.store.cancel
        self.assertFalse(self.engine.step())
        self.assertEqual(self.env['resumes'], 0)

    def test_successfully_sent_but_lost_ack_never_retries_after_restart(self):
        self.low()
        self.engine.step()
        self.restore()
        self.env['resume_timeout'] = True
        self.assertFalse(self.engine.step())
        self.assertEqual(self.env['resumes'], 1)
        self.assertEqual(self.store.data['phase'], 'uncertain')
        restarted = Store(self.tmp.name, THREAD)
        with self.assertRaises(RuntimeError):
            restarted.begin()
        self.assertEqual(self.env['resumes'], 1)

    def test_interrupt_lost_ack_never_retries(self):
        self.low()
        self.env['interrupt_timeout'] = True
        self.assertFalse(self.engine.step())
        self.assertEqual(self.env['interrupts'], 1)
        with self.assertRaises(RuntimeError):
            Store(self.tmp.name, THREAD).begin()

    def test_crash_after_persisting_intent_does_not_send(self):
        self.store.claim('resume', 'turn1', 'message1')
        self.assertFalse(self.engine.step())
        self.assertEqual(self.env['resumes'], 0)

    def test_confirmed_pause_survives_process_restart(self):
        self.low()
        self.engine.step()
        self.store.close()
        self.store = Store(self.tmp.name, THREAD)
        self.store.acquire()
        self.addCleanup(self.store.close)
        self.store.begin()
        self.engine = Engine(self.store, lambda: FakeDesktop(self.env), lambda: self.env['usage'], clock=lambda: self.now)
        self.restore()
        self.engine.step()
        self.assertEqual(self.env['resumes'], 1)

    def test_structured_quota_failure_is_recovered_without_fake_interrupt(self):
        self.env['state'] = state('failed', error={'codexErrorInfo': 'UsageLimitExceeded'})
        self.engine.step()
        self.engine.step()
        self.assertEqual(self.env['interrupts'], 0)
        self.assertEqual(self.env['resumes'], 1)

    def test_quota_words_inside_other_error_do_not_authorize_resume(self):
        self.env['state'] = state('failed', error={'message': 'quota mentioned in test output'})
        self.assertFalse(self.engine.step())
        self.assertEqual(self.env['resumes'], 0)

    def test_budget_stops_without_extra_resume(self):
        self.engine.max_resumes = 1
        self.store.data['resumeCount'] = 1
        self.low()
        self.engine.step()
        self.restore()
        self.assertFalse(self.engine.step())
        self.assertEqual(self.env['resumes'], 0)
        self.assertEqual(self.env['interrupts'], 0)

    def test_crash_after_pause_record_before_send_does_not_own_manual_interruption(self):
        self.store.save(paused={'turnId': 'turn1', 'identity': identity(state()),
                               'cause': 'threshold', 'at': 1000}, phase='interrupting')
        self.env['state'] = state('interrupted')
        self.restore()
        self.assertFalse(self.engine.step())
        self.assertEqual(self.env['resumes'], 0)
        self.assertEqual(self.store.data['phase'], 'attention')

    def test_wrong_interruption_receipt_does_not_resume(self):
        self.low()
        self.engine.step()
        self.store.data['attempts']['interrupt:turn1']['resultId'] = 'different-turn'
        self.restore()
        self.assertFalse(self.engine.step())
        self.assertEqual(self.env['resumes'], 0)

    def test_status_uses_live_process_lock_and_detects_crash(self):
        live = statuses(self.tmp.name)[0]
        self.assertTrue(live['monitoring'])
        self.store.close()
        dead = statuses(self.tmp.name)[0]
        self.assertFalse(dead['monitoring'])
        self.assertEqual(dead['displayPhase'], 'offline')

    def test_cancellation_arriving_during_enrollment_is_preserved(self):
        self.store.cancel()
        requested = self.store.cancel_path.stat().st_mtime_ns - 1
        with self.assertRaises(RuntimeError):
            self.store.begin(requested)
        self.assertTrue(self.store.cancel_path.exists())

    def test_explicit_new_start_can_replace_older_cancellation(self):
        self.store.cancel()
        self.store.begin(self.store.cancel_path.stat().st_mtime_ns + 1)
        self.assertTrue(self.store.live())

    def test_acquiring_lock_reloads_updated_ledger(self):
        self.store.close()
        stale = Store(self.tmp.name, THREAD)
        self.store.save(resumeCount=7)
        stale.acquire()
        self.addCleanup(stale.close)
        self.assertEqual(stale.data['resumeCount'], 7)

    def test_new_manual_enrollment_clears_stale_resume_grace(self):
        self.store.save(phase='attention', lastResumedTurn='old-turn', graceUntil=99999)
        self.store.begin()
        self.assertNotIn('lastResumedTurn', self.store.data)
        self.assertNotIn('graceUntil', self.store.data)

    def test_process_lock_prevents_second_monitor(self):
        second = Store(self.tmp.name, THREAD)
        with self.assertRaises(RuntimeError):
            second.acquire()

    def test_busy_snapshot_before_send_is_retryable_because_no_request_was_written(self):
        self.low()
        def change_settings():
            self.env['state']['latestModel'] = 'changed'
        self.env['before_interrupt'] = change_settings
        self.assertTrue(self.engine.step())
        self.assertIsNone(self.store.data['paused'])
        self.assertEqual(self.env['interrupts'], 0)
        self.assertEqual(self.store.data['attempts']['interrupt:turn1']['status'], 'not_sent')
        self.env.pop('before_interrupt')
        self.assertTrue(self.engine.step())
        self.assertEqual(self.env['interrupts'], 1)

    def test_manual_stop_during_pre_send_validation_is_not_owned_by_guard(self):
        self.low()
        self.env['before_interrupt'] = lambda: self.env.update(state=state('interrupted'))
        self.engine.step()
        self.assertFalse(self.engine.step())
        self.assertEqual(self.env['interrupts'], 0)
        self.assertEqual(self.env['resumes'], 0)

    def test_state_contains_no_conversation_or_credentials(self):
        self.low()
        self.engine.step()
        saved = self.store.path.read_text()
        self.assertNotIn('synthetic/project', saved)
        self.assertNotIn('original-model', saved)
        self.assertNotIn('synthetic', saved)
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)


class PolicyTests(unittest.TestCase):
    def test_contradictory_runtime_waits_for_sync(self):
        s = state('completed')
        s['threadRuntimeStatus'] = {'type': 'active', 'activeFlags': []}
        self.assertEqual(task(s).kind, 'connecting')
        s = state()
        s['threadRuntimeStatus'] = {'type': 'idle'}
        self.assertEqual(task(s).kind, 'connecting')

    def test_missing_active_flags_are_not_assumed_empty(self):
        s = state()
        s['threadRuntimeStatus'] = {'type': 'active'}
        self.assertEqual(task(s).kind, 'blocked')

    def test_goal_lifecycle_states_are_not_auto_resumed(self):
        for status in ('paused', 'complete', 'blocked', 'budgetLimited'):
            s = state()
            s['threadGoal'] = {'status': status}
            self.assertEqual(task(s).kind, 'blocked')

    def test_malformed_canonical_history_is_refused(self):
        for history in (None, [], {'islands': [None]}):
            s = state()
            s['turnHistory'] = {'kind': 'canonical', 'history': history}
            self.assertEqual(task(s).kind, 'blocked')

    def test_unknown_quota_response_types_are_refused(self):
        for response in (None, [], 'error'):
            self.assertFalse(quota(response, 1000).known)
    def test_missing_and_invalid_quota_is_unknown(self):
        for response in ({}, {'rateLimits': {}}, usage(used=float('nan')), usage(used=True)):
            q = quota(response, 1000)
            self.assertFalse(q.known)
            self.assertFalse(q.ready)
            self.assertFalse(q.low)

    def test_stale_reset_data_never_authorizes_resume(self):
        q = quota(usage(primary_reset=999), 1000)
        self.assertFalse(q.ready)

    def test_only_hourly_reset_controls_wait_even_when_weekly_reset_is_later(self):
        q = quota(usage(96, 99), 1000)
        self.assertTrue(q.low)
        self.assertEqual(q.next_check, 1115)

    def test_weekly_percentage_does_not_control_recovery(self):
        for weekly in (95, 100):
            q = quota(usage(90, weekly), 1000)
            self.assertTrue(q.ready)
            self.assertFalse(q.low)
            self.assertEqual(q.windows[1]['remaining'], 100 - weekly)

    def test_invalid_missing_or_stale_weekly_data_does_not_block_hourly_decisions(self):
        for secondary in (None, [], {}, {'usedPercent': float('nan')},
                          {'usedPercent': 99, 'resetsAt': 999, 'windowDurationMins':10080}):
            for used in (90, 95):
                response = usage(used)
                response['rateLimitsByLimitId']['codex']['secondary'] = secondary
                q = quota(response, 1000)
                self.assertTrue(q.known)
                self.assertEqual(q.low, used == 95)
                self.assertEqual(q.ready, used == 90)

    def test_unrecognized_primary_duration_never_controls_hourly_monitor(self):
        response = usage(99)
        response['rateLimitsByLimitId']['codex']['primary']['windowDurationMins'] = 10080
        q = quota(response, 1000)
        self.assertFalse(q.known)
        self.assertFalse(q.low)
        self.assertFalse(q.ready)

    def test_actual_server_rejection_blocks_dispatch_without_weekly_reset_wait(self):
        response = usage(10, 100)
        response['rateLimitsByLimitId']['codex']['rateLimitReachedType'] = 'rate_limit_reached'
        q = quota(response, 1000)
        self.assertFalse(q.low)
        self.assertFalse(q.ready)
        self.assertEqual(q.next_check, 1060)

    def test_no_fake_weekly_window_when_it_is_absent(self):
        response = usage()
        response['rateLimitsByLimitId']['codex']['secondary'] = None
        q = quota(response, 1000)
        self.assertTrue(q.ready)
        self.assertEqual(len(q.windows), 1)

    def test_selects_only_intended_bucket(self):
        response = usage()
        response['rateLimitsByLimitId']['other-model'] = usage(99)['rateLimitsByLimitId']['codex']
        self.assertFalse(quota(response, 1000).low)
        self.assertTrue(quota(response, 1000, limit_id='other-model').low)

    def test_incomplete_canonical_tail_refused(self):
        s = state()
        s['turnHistory'] = {'kind': 'canonical', 'history': {'islands': [{'newerBoundary': {'status': 'hasMore'}}]}}
        self.assertEqual(task(s).kind, 'blocked')

    def test_pause_status_change_does_not_change_work_identity(self):
        self.assertEqual(identity(state()), identity(state('interrupted')))


class TransportTests(unittest.TestCase):
    def test_real_unix_framing_targets_owner_and_expected_turn_and_fixed_prompt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = root / 'ChatGPT.app'
            (app / 'Contents').mkdir(parents=True)
            (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
                'CFBundleIdentifier': 'com.openai.codex', 'CFBundleShortVersionString': '999.0.0',
                'CFBundleVersion': '99999'}))
            home = root / 'home'
            (home / 'ipc').mkdir(parents=True, mode=0o700)
            listener = socket.socket(socket.AF_UNIX)
            listener.bind(str(home / 'ipc/ipc.sock'))
            os.chmod(home / 'ipc/ipc.sock', 0o600)
            listener.listen()
            captured, errors = [], []
            current = state()
            current.pop('ephemeral')
            rollout = home / 'sessions' / ('rollout-fixture-' + THREAD + '.jsonl')
            rollout.parent.mkdir()
            rollout.write_text('fixture')
            current.update(sessionId=THREAD, rolloutPath=str(rollout))
            def exact(sock, size):
                buf = b''
                while len(buf) < size:
                    chunk = sock.recv(size - len(buf))
                    if not chunk:
                        raise EOFError()
                    buf += chunk
                return buf
            def send(sock, msg):
                raw = json.dumps(msg).encode()
                # Deliberate fragmented writes exercise exact-length framing.
                sock.sendall(struct.pack('<I', len(raw)) + raw[:10])
                sock.sendall(raw[10:])
            def serve():
                try:
                    with listener.accept()[0] as sock:
                        sock.settimeout(5)
                        while True:
                            msg = json.loads(exact(sock, struct.unpack('<I', exact(sock, 4))[0]))
                            if msg['type'] == 'broadcast':
                                if msg['params'].get('following'):
                                    send(sock, {'type': 'broadcast', 'method': 'thread-stream-state-changed',
                                        'sourceClientId': 'owner', 'version': 11,
                                        'params': {'conversationId': THREAD, 'hostId': 'local',
                                            'change': {'type': 'snapshot', 'revision': 1, 'conversationState': current}}})
                                continue
                            method = msg['method']
                            result = {}
                            if method == 'initialize':
                                result = {'clientId': 'guard'}
                            elif method == 'thread-owner-discovery':
                                pass
                            elif method == 'thread-follower-interrupt-turn':
                                captured.append(msg)
                                current.update(state('interrupted'))
                                current.pop('ephemeral')
                                result = {'ok': True, 'interruptedTurnId': 'turn1'}
                            elif method == 'thread-follower-start-turn':
                                captured.append(msg)
                                result = {'result': {'turn': {'id': 'turn2'}}}
                            send(sock, {'type': 'response', 'requestId': msg['requestId'], 'method': method,
                                        'resultType': 'success', 'handledByClientId': 'owner', 'result': result})
                except EOFError:
                    pass
                except Exception as exc:
                    errors.append(exc)
            worker = threading.Thread(target=serve, daemon=True)
            worker.start()
            try:
                with Desktop(home, str(app), timeout=3) as d:
                    self.assertEqual(task(d.snapshot(THREAD)).kind, 'running')
                    self.assertEqual(d.interrupt(THREAD, 'turn1', lambda s: task(s).kind == 'running', lambda: True), 'turn1')
                    self.assertEqual(d.resume(THREAD, 'fixed-message', lambda s: task(s).kind == 'interrupted', lambda: True), 'turn2')
            finally:
                worker.join(5)
                listener.close()
            self.assertEqual(errors, [])
            self.assertEqual(captured[0]['version'], 4)
            self.assertEqual(captured[0]['targetClientId'], 'owner')
            self.assertEqual(captured[0]['params']['expectedTurnId'], 'turn1')
            request = captured[1]['params']['turnStart']['request']
            self.assertEqual(request['clientUserMessageId'], 'fixed-message')
            self.assertEqual(set(request), {'threadId', 'clientUserMessageId', 'input'})


if __name__ == '__main__':
    unittest.main()
