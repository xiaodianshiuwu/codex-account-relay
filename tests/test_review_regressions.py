import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from argparse import Namespace
from unittest.mock import patch

from codex_relay.auto_config import load_auto_settings, save_auto_settings
from codex_relay.appserver import parse_usage_response
from codex_relay.authfile import write_auth, read_auth
from codex_relay.cli import _auto_once, main
from codex_relay.codex_env import _role_for_command
from codex_relay.guard_cli import backend_command, child_environment, default_state_dir
from codex_relay.store import _safe_component
from codex_relay.switch import switch, SwitchOptions
from codex_relay.threads import list_threads
from codex_relay.usage import usage_known, refresh_usage, idle_check
from codex_relay.util import RelayError, redact
from desktop_guard import settings
from tests.helpers import new_store, make_auth, write_state_db


class ReviewRegressions(unittest.TestCase):
    def test_nonfinite_and_boolean_auto_settings_never_enable_actions(self):
        for key, value in [('interval', float('inf')), ('interval', True), ('remaining_threshold', float('nan')), ('enabled', 'true')]:
            with self.subTest(key=key, value=value), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)/'auto.json'
                path.write_text(json.dumps({'enabled': True, key: value}))
                self.assertFalse(load_auto_settings(path)['enabled'])
                with self.assertRaises(RelayError): save_auto_settings({key: value}, path)

    def test_legacy_auto_enabled_does_not_grant_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'auto.json'; path.write_text('{"enabled": true}')
            self.assertFalse(load_auto_settings(path)['allow_chatgpt_restart'])

    def test_auto_update_preserves_unknown_keys_strategy_and_interval(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'auto.json'
            save_auto_settings({'interval': 1234, 'strategy': 'next', 'future': 'retained'}, path)
            save_auto_settings({'prompt': 'x'*5000}, path)
            self.assertEqual(load_auto_settings(path)['interval'], 1234)
            self.assertEqual(load_auto_settings(path)['strategy'], 'next')
            self.assertEqual(load_auto_settings(path)['future'], 'retained')
            self.assertEqual(len(load_auto_settings(path)['prompt']), 5000)

    def test_respect_settings_stops_before_network_or_auth_access(self):
        with patch('codex_relay.cli.load_auto_settings', return_value={'enabled': False, 'allow_chatgpt_restart': True}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(_auto_once(None, Namespace(respect_settings=True)), 2)

    def test_invalid_cli_config_does_not_enable_automation(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'CODEX_RELAY_HOME': tmp, 'CODEX_HOME': tmp}), contextlib.redirect_stderr(io.StringIO()):
            for argv in (['--interval','nan'], ['--interval','-1'], ['--remaining-threshold','101'], ['--threshold','20','--remaining-threshold','10']):
                self.assertEqual(main(['auto','--enable','--once', *argv]), 1)
                self.assertFalse(load_auto_settings()['enabled'])

    def test_missing_nan_stale_or_out_of_range_quota_not_considered_available(self):
        for value in ({}, {'primary': {'used_percent': None}}, {'primary': {'used_percent': float('nan')}}, {'primary': {'used_percent': -1}}, {'primary': {'used_percent': 5, 'resets_at': time.time()-1}}):
            with self.subTest(value=value): self.assertFalse(usage_known(value))
        self.assertTrue(usage_known({'primary': {'used_percent': 5, 'resets_at': time.time()+60}}))

    def test_codex_bucket_takes_precedence_over_legacy_and_unrelated_buckets(self):
        value = parse_usage_response({}, {'rateLimits': {'primary': {'usedPercent': 1}}, 'rateLimitsByLimitId': {'other': {'primary': {'usedPercent': 2}}, 'codex': {'primary': {'usedPercent': 90}}}})
        self.assertEqual(value['primary']['used_percent'], 90)
        self.assertIsNone(parse_usage_response({}, {'rateLimitsByLimitId': {'other': {'primary': {'usedPercent': 2}}}})['primary'])

    def test_daemon_role_is_reachable_and_relay_with_spaces_excluded(self):
        self.assertEqual(_role_for_command('/usr/bin/codex app-server daemon start'), 'daemon')
        self.assertEqual(_role_for_command('/Applications/Codex Relay 2.app/Contents/MacOS/CodexRelay'), 'other')

    def test_special_profile_components_cannot_escape_profiles_directory(self):
        self.assertNotEqual(_safe_component('..'), '..')
        self.assertNotEqual(_safe_component('.'), '.')

    def test_actual_auth_protects_active_profile_even_when_state_is_stale(self):
        store = new_store(); a = store.add_profile('a', make_auth('acct-a')); b = store.add_profile('b', make_auth('acct-b'))
        write_auth(store.paths.auth_path, make_auth('acct-b'))
        store.state['active_profile_id'] = a['id']
        with self.assertRaises(RelayError): store.remove_profile('b')
        store.remove_profile('a')
        self.assertEqual(store.current_profile()['id'], b['id'])

    def test_backups_unlimited_by_default_and_explicit_retention_supported(self):
        store = new_store()
        for _ in range(22): store.backup('test')
        self.assertEqual(len(list(store.paths.backups_dir.iterdir())), 22)
        store.state['settings']['backup_keep'] = 3
        store.backup('configured')
        self.assertEqual(len(list(store.paths.backups_dir.iterdir())), 3)

    def test_force_refresh_cannot_rotate_live_active_account(self):
        store = new_store(); auth = make_auth('acct-a'); profile = store.add_profile('a', auth); write_auth(store.paths.auth_path, auth)
        with patch('codex_relay.usage.codex_env.codex_processes', return_value=[object()]), patch('codex_relay.usage.fetch_usage_http', return_value={'primary': {'used_percent': 10}}) as http, patch('codex_relay.usage.verify_profile') as verify:
            refresh_usage(store, profile, force_refresh=True)
            http.assert_called_once(); verify.assert_not_called()
        self.assertEqual(read_auth(store.paths.auth_path), auth)

    def test_old_schema_search_only_references_existing_columns(self):
        store = new_store(); write_state_db(store.paths, [])
        with sqlite3.connect(store.paths.state_db) as db:
            db.execute('ALTER TABLE threads DROP COLUMN name')
            db.execute('ALTER TABLE threads DROP COLUMN preview')
        self.assertEqual(list_threads(store.paths, search='test'), [])

    def test_long_quiet_turn_does_not_become_idle(self):
        store = new_store()
        turn = {'status': 'inProgress', 'thread_id': 'unknown', 'turn_id': 't', 'started_at': time.time()-10000}
        with patch('codex_relay.usage.active_turn_status', return_value=[turn]), patch('codex_relay.usage.get_thread', return_value=None), patch('codex_relay.usage.codex_env.app_running', return_value=True), patch('codex_relay.usage.codex_env.desktop_app_server_processes', return_value=[]):
            self.assertFalse(idle_check(store)['idle'])

    def test_no_restart_option_never_launches_desktop(self):
        store = new_store(); a = store.add_profile('a', make_auth('acct-a')); store.add_profile('b', make_auth('acct-b')); write_auth(store.paths.auth_path, make_auth('acct-a'))
        with patch('codex_relay.switch.preflight_processes', return_value=[]), patch('codex_relay.switch.idle_check', return_value={'idle':True}), patch('codex_relay.switch.codex_env.app_running', return_value=False), patch('codex_relay.switch.codex_env.desktop_bundle_processes', return_value=[]), patch('codex_relay.switch.codex_env.daemon_running', return_value=False), patch('codex_relay.switch.codex_env.launch_desktop') as launch:
            result = switch(store, SwitchOptions(target='b', thread='none', verify=False, mode='desktop', restart=False, allow_chatgpt_restart=True))
            self.assertTrue(result['changed']); launch.assert_not_called()

    def test_guard_uses_same_frozen_entrypoint_and_fresh_runtime(self):
        with patch('sys.frozen', True, create=True), patch('codex_relay.guard_cli.ENTRY', Path('/fixture/codex-relay')):
            self.assertEqual(backend_command(), ['/fixture/codex-relay', 'guard'])
            self.assertEqual(child_environment()['PYINSTALLER_RESET_ENVIRONMENT'], '1')

    def test_guard_isolates_state_and_accepts_long_prompt_and_unbounded_interval(self):
        with patch.dict(os.environ, {'CODEX_RELAY_HOME':'/fixture/relay'}, clear=True):
            self.assertEqual(default_state_dir(), Path('/fixture/relay/guard'))
        self.assertEqual(settings.validate({'interval': 0.25, 'continuation':'x'*5000})['interval'], 0.25)
        self.assertEqual(settings.validate({'interval': 9999})['interval'], 9999)

    def test_embedded_credentials_redacted_in_diagnostics(self):
        self.assertNotIn('abcdefgh.ijklmnop.qrstuvwx', redact('failure abcdefgh.ijklmnop.qrstuvwx tail'))
        self.assertNotIn('private-token', redact('HTTP Authorization: Bearer private-token'))

    def test_live_http_failure_is_not_reported_as_success_or_relogin(self):
        store=new_store();auth=make_auth('acct-a');profile=store.add_profile('a',auth);write_auth(store.paths.auth_path,auth)
        with patch('codex_relay.usage.codex_env.codex_processes',return_value=[object()]),patch('codex_relay.usage.fetch_usage_http',side_effect=RelayError('fixture network failure')):
            with self.assertRaises(RelayError):refresh_usage(store,profile)
        self.assertEqual(profile['status'],'error')
        self.assertIn('network failure',store.profile_summary(profile)['last_error'])
    def test_missing_session_database_has_empty_successful_response(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'CODEX_RELAY_HOME':tmp+'/relay','CODEX_HOME':tmp+'/codex'}),contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(['sessions','--json']),0)
            self.assertEqual(json.loads(out.getvalue())['threads'],[])

    def test_parallel_cli_writers_preserve_all_profiles(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, CODEX_RELAY_HOME=tmp+'/relay', CODEX_HOME=tmp+'/codex', PYTHONPATH=str(root))
            Path(tmp+'/codex').mkdir()
            paths = []
            for i in range(6):
                auth = Path(tmp)/f'fixture-{i}.json'; auth.write_text(json.dumps(make_auth(f'acct-{i}')))
                paths.append(subprocess.Popen([sys.executable,'-m','codex_relay','import',f'alias{i}',str(auth),'--json'], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE))
            for process in paths:
                out, err = process.communicate(timeout=15)
                self.assertEqual(process.returncode, 0, err.decode())
            saved=json.loads(Path(tmp+'/relay/state.json').read_text())
            self.assertEqual(len(saved['profiles']),6)
