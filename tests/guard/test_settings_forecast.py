import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codex_relay import guard_cli as guard
from desktop_guard import settings
from desktop_guard.forecast import forecast
from desktop_guard.desktop import Desktop
from desktop_guard.remote_control import RemoteController
from desktop_guard.engine import Engine
from desktop_guard.store import Store
from .test_guard import THREAD, state, usage, FakeDesktop
from .test_remote import DESCRIPTOR
from .test_remote_control import remote_state


class SettingsTests(unittest.TestCase):
    def test_rejects_invalid_hysteresis_nonfinite_types_and_message(self):
        for changes in ({'threshold': 10, 'resumeRemaining': 10}, {'threshold': -1},
                        {'threshold': 99, 'resumeRemaining': 98}, {'interval': float('nan')},
                        {'threshold': True}, {'continuation': ' \n'},
                        {'continuation': 'x\0y'}, {'extra': 1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError): settings.validate(changes)
        self.assertEqual(settings.validate({'threshold': 99, 'resumeRemaining': 100})['threshold'], 99)

    def test_atomic_private_settings_roundtrip_and_no_monitor_record(self):
        from desktop_guard.store import statuses
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(settings.load(tmp), settings.DEFAULTS)
            value = dict(settings.DEFAULTS, threshold=25, resumeRemaining=40, continuation='继续原任务\n保留进度')
            path = Path(tmp)/'settings.json'
            settings.write(path, value)
            self.assertEqual(settings.load(tmp), value)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(statuses(tmp), [])

    def test_background_freezes_message_in_private_file_not_argv(self):
        from types import SimpleNamespace
        secret = '只执行下一步\n不要重复历史'
        with tempfile.TemporaryDirectory() as tmp:
            args = SimpleNamespace(state_dir=Path(tmp), thread_id=THREAD, host='local', app=Path('/fixture'),
                home=Path('/fixture'), threshold=20, resume_remaining=30, interval=30,
                limit_id='codex', max_resumes=0, requested_at_ns=1, continuation=secret)
            def inspect_spawn(command, **kwargs):
                self.assertNotIn(secret, ' '.join(command))
                cfg = Path(command[command.index('--config-file')+1])
                self.assertEqual(json.loads(cfg.read_text())['continuation'], secret)
                self.assertEqual(cfg.stat().st_mode & 0o777, 0o600)
                raise OSError('fixture spawn failure')
            with patch.object(guard, 'check', return_value={'canMonitor': True, 'kind': 'running'}), \
                 patch.object(guard.subprocess, 'Popen', side_effect=inspect_spawn):
                with self.assertRaises(OSError): guard.background(args)
            self.assertEqual(list((Path(tmp)/'configurations').glob('*.json')), [])

    def test_local_and_remote_preserve_custom_unicode_message_and_execution_settings(self):
        text = '按原计划接着做\n不要重复完成项。'
        d = Desktop('/fixture'); d.continuation = text
        with patch.object(d, 'mutate', return_value={'result': {'result': {'turn': {'id': 'new'}}}}) as call:
            self.assertEqual(d.resume(THREAD, 'message', lambda s: True), 'new')
            request = call.call_args.args[2]['turnStart']['request']
            self.assertEqual(request['input'][0]['text'], text)
            self.assertNotIn('model', request)
        r = RemoteController(DESCRIPTOR); r.continuation = text
        with patch.object(r, '_validate', return_value=remote_state('interrupted')), \
             patch.object(r, '_request', return_value={'turn': {'id': 'new'}}) as call:
            self.assertEqual(r.resume(THREAD, 'message', lambda s: True), 'new')
            request = call.call_args.args[1]
            self.assertEqual(request['input'][0]['text'], text)
            self.assertNotIn('model', request)
            self.assertNotIn('cwd', request)

    def test_engine_applies_custom_threshold_and_freezes_original_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp, THREAD); store.begin()
            env = {'state': state(), 'interrupts': 0, 'resumes': 0}
            reader = [usage(used=80)]
            app = FakeDesktop(env)
            e = Engine(store, lambda: app, lambda: reader[0], threshold=20,
                resume_remaining=30, clock=lambda: 1000, continuation='自定义接续')
            self.assertTrue(e.step()); self.assertEqual(env['interrupts'], 1)
            reader[0] = usage(used=70, weekly=100)
            self.assertTrue(e.step()); self.assertEqual(env['resumes'], 1)
            self.assertEqual(app.continuation, '自定义接续')
            self.assertEqual(store.data['resumeCount'], 1)


class ForecastTests(unittest.TestCase):
    def test_interface_reset_and_estimated_later_windows_use_actual_duration(self):
        raw = usage(primary_reset=2000)
        f = forecast(raw, 1000)
        self.assertEqual(len(f['slots']), 4)
        self.assertEqual(f['nextReset'], 2000)
        self.assertEqual(f['slots'][1]['start'], 2000+300*60)
        self.assertTrue(f['slots'][0]['resetConfirmed'])
        self.assertFalse(f['slots'][1]['resetConfirmed'])
        self.assertIn('估算', f['notice'])
        self.assertNotIn('ready', f['slots'][0])
        raw['rateLimitsByLimitId']['codex']['primary']['windowDurationMins'] = 60
        self.assertEqual(forecast(raw, 1000)['slots'][1]['start'], 5600)

    def test_expired_or_unknown_quota_does_not_invent_dates(self):
        for raw in ({}, usage(primary_reset=999), usage(primary_reset=1000)):
            self.assertEqual(forecast(raw, 1000)['slots'], [])

    def test_service_rejection_and_custom_resume_threshold_affect_ready_now(self):
        raw = usage(used=75)
        self.assertFalse(forecast(raw, 1000, 20, 30)['readyNow'])
        self.assertTrue(forecast(raw, 1000, 20, 25)['readyNow'])
        raw['rateLimitsByLimitId']['codex']['rateLimitReachedType'] = 'rate_limit_reached'
        self.assertFalse(forecast(raw, 1000)['readyNow'])
