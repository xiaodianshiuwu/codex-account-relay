"""Restart detection and handoff regressions; only synthetic homes and apps."""
import contextlib
import io
import json
import os
import sqlite3
import time
import unittest
from datetime import datetime
from unittest.mock import patch

from codex_relay.authfile import read_auth, write_auth
from codex_relay.cli import _auto_once, build_parser
from codex_relay.codex_env import ProcessInfo
from codex_relay.usage import idle_check, idle_blocked_message
from codex_relay.switch import SwitchOptions, switch
from codex_relay.util import RelayError
from desktop_guard.desktop import ThreadUnavailable
from desktop_guard.policy import task
from tests.helpers import new_store, make_auth, write_state_db, write_active_turn
from tests.test_desktop_continue import FakeApp
from tests.guard.test_guard import state, THREAD

OLD = '22222222-2222-4222-8222-222222222222'

class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.store = new_store()
        self.old = time.time() - 24*86400
        self.generation = time.time() - 600
        write_state_db(self.store.paths, [{'id':OLD,'rollout_path':'sessions/missing.jsonl'}])
        write_active_turn(self.store.paths, OLD, 'abandoned-turn')
        with contextlib.closing(sqlite3.connect(self.store.paths.thread_history_db)) as db, db:
            db.execute('UPDATE thread_turns SET started_at=?', (int(self.old),))
        self.process = ProcessInfo(4242, 1, datetime.fromtimestamp(self.generation), 'synthetic desktop', 'desktop-main')
        for target, value in [('desktop_main_processes',[self.process]), ('app_running',True), ('desktop_app_server_processes',[])]:
            p = patch('codex_relay.usage.codex_env.'+target, return_value=value)
            p.start(); self.addCleanup(p.stop)

    def check(self, failure=None, current=None):
        with patch('codex_relay.desktop_continue.runtime_task', side_effect=failure,
                   return_value=(task(current), current) if current else None):
            return idle_check(self.store)

    def test_reported_24_day_old_missing_rollout_and_no_owner_is_abandoned(self):
        result = self.check(ThreadUnavailable('no-client-found'))
        self.assertTrue(result['idle'])
        self.assertEqual(result['ignored_history'][0]['reason'], 'unowned-before-desktop-start')
        with contextlib.closing(sqlite3.connect(self.store.paths.thread_history_db)) as db, db:
            self.assertEqual(db.execute('SELECT status FROM thread_turns').fetchone()[0], 'inProgress')

    def test_quiet_old_task_with_live_owner_never_expires(self):
        result = self.check(current=state())
        self.assertFalse(result['idle'])
        self.assertEqual(result['active'][0]['evidence'], 'desktop-runtime')
        self.assertEqual(result['active'][0]['turn_id'], 'turn1')

    def test_broken_transport_is_not_evidence_of_an_abandoned_turn(self):
        for failure in [TimeoutError('timeout'), OSError('socket missing'), RuntimeError('protocol changed'), TypeError('incompatible state'), AttributeError('incomplete history')]:
            with self.subTest(failure=failure):
                self.assertFalse(self.check(failure)['idle'])

    def test_no_owner_in_current_desktop_generation_remains_uncertain(self):
        with contextlib.closing(sqlite3.connect(self.store.paths.thread_history_db)) as db, db:
            db.execute('UPDATE thread_turns SET started_at=?', (int(time.time()-10),))
        self.assertFalse(self.check(ThreadUnavailable())['idle'])

    def test_unknown_desktop_start_remains_uncertain(self):
        self.process.started = None
        self.assertFalse(self.check(ThreadUnavailable())['idle'])

    def test_rollout_changed_in_current_generation_remains_uncertain(self):
        path = self.store.paths.codex_home/'sessions/missing.jsonl'
        path.parent.mkdir(); path.write_text('{}\n')
        self.assertFalse(self.check(ThreadUnavailable())['idle'])

    def test_terminal_runtime_overrides_history_even_if_guard_is_blocked(self):
        for status in ['completed','interrupted','failed']:
            current = state(status); current['threadGoal'] = {'status':'paused'}
            self.assertEqual(task(current).kind, 'blocked')
            self.assertTrue(self.check(current=current)['idle'])

    def test_runtime_mismatch_does_not_authorize_restart(self):
        current=state(); current['threadRuntimeStatus']={'type':'idle'}
        self.assertFalse(self.check(current=current)['idle'])

    def test_multiple_unfinished_history_rows_do_not_duplicate_a_live_chat(self):
        write_active_turn(self.store.paths,OLD,'another-old-turn')
        result=self.check(current=state())
        self.assertEqual(len(result['active']),1)

    def test_force_entry_skips_only_idle_check_and_uses_normal_quit(self):
        self.store.add_profile('a',make_auth('acct-a')); self.store.add_profile('b',make_auth('acct-b'))
        write_auth(self.store.paths.auth_path,make_auth('acct-a'))
        with patch('codex_relay.switch.preflight_processes',return_value=[]) as preflight, patch('codex_relay.switch.idle_check') as idle, patch('codex_relay.switch.codex_env.desktop_bundle_processes',return_value=[]), patch('codex_relay.switch.codex_env.quit_desktop',return_value=[]) as quit_app, patch('codex_relay.switch.codex_env.daemon_running',return_value=False), patch('codex_relay.switch.codex_env.launch_desktop'), patch('codex_relay.switch.codex_env.wait_for_desktop_app_server',return_value=self.process):
            result=switch(self.store,SwitchOptions(target='b',thread='none',verify=False,allow_chatgpt_restart=True,confirmed_idle=True))
            self.assertTrue(result['changed']); idle.assert_not_called()
            preflight.assert_called_once_with('desktop',False)
            quit_app.assert_called_once_with(timeout=20.0,force=False)
        self.assertEqual(read_auth(self.store.paths.auth_path)['tokens']['account_id'],'acct-b')

    def test_force_entry_never_skips_process_conflicts_or_restart_permission(self):
        self.store.add_profile('a',make_auth('acct-a'));self.store.add_profile('b',make_auth('acct-b'))
        write_auth(self.store.paths.auth_path,make_auth('acct-a'))
        with patch('codex_relay.switch.preflight_processes',side_effect=RelayError('other CLI writer')):
            with self.assertRaisesRegex(RelayError,'other CLI writer'):
                switch(self.store,SwitchOptions(target='b',thread='none',verify=False,confirmed_idle=True,dry_run=True))
        with self.assertRaises(RelayError):
            switch(self.store,SwitchOptions(target='b',thread='none',verify=False,confirmed_idle=True))
        self.assertEqual(read_auth(self.store.paths.auth_path)['tokens']['account_id'],'acct-a')

    def test_activity_message_translates_runtime_detail_in_all_four_languages(self):
        for lang in ['zh-Hans','zh-Hant','en','ja']:
            with self.subTest(lang=lang),patch.dict(os.environ,{'CODEX_RELAY_LANG':lang}):
                text=idle_blocked_message({'active':[{'thread_id':OLD,'turn_id':'t','evidence':'desktop-runtime'}]})
                self.assertIn(OLD,text)
                self.assertNotIn('Another chat',text)
                if lang in ('en','ja'):self.assertNotIn('桌面端',text)

class AutomaticHandoffIntegrationTests(unittest.TestCase):
    def test_auto_passes_abandoned_record_then_interrupts_switches_and_resumes_original_chat(self):
        store=new_store(); a=store.add_profile('a',make_auth('acct-a'));b=store.add_profile('b',make_auth('acct-b'))
        a['usage']={'primary':{'used_percent':99}}; b['usage']={'primary':{'used_percent':10}}
        write_auth(store.paths.auth_path,make_auth('acct-a'))
        write_state_db(store.paths,[{'id':OLD,'rollout_path':'sessions/absent.jsonl'},{'id':THREAD,'rollout_path':'sessions/selected.jsonl'}])
        write_active_turn(store.paths,OLD,'old-turn')
        with contextlib.closing(sqlite3.connect(store.paths.thread_history_db)) as db, db:db.execute('UPDATE thread_turns SET started_at=? WHERE thread_id=?',(1,OLD))
        write_active_turn(store.paths,THREAD,'turn1')
        app=FakeApp(state())
        process=ProcessInfo(4242,1,datetime.now(),'fake','desktop-main')
        args=build_parser().parse_args(['auto','--once','--json','--thread',THREAD,'--prompt','continue original work','--remaining-threshold','10','--allow-chatgpt-restart','--yes'])
        output=io.StringIO()
        with contextlib.ExitStack() as stack:
            for target,kwargs in [
                ('codex_relay.desktop_continue.controller',{'return_value':app}),
                ('codex_relay.cli.refresh_usage',{'return_value':None}),
                ('codex_relay.switch.preflight_processes',{'return_value':[]}),
                ('codex_relay.switch.codex_env.desktop_main_processes',{'return_value':[process]}),
                ('codex_relay.switch.codex_env.app_running',{'return_value':True}),
                ('codex_relay.switch.codex_env.desktop_app_server_processes',{'return_value':[]}),
                ('codex_relay.switch.codex_env.desktop_bundle_processes',{'return_value':[]}),
                ('codex_relay.switch.codex_env.quit_desktop',{'return_value':[]}),
                ('codex_relay.switch.codex_env.daemon_running',{'return_value':False}),
                ('codex_relay.switch.codex_env.launch_desktop',{'return_value':None}),
                ('codex_relay.switch.codex_env.wait_for_desktop_app_server',{'return_value':process}),
                ('codex_relay.switch.codex_env.wait_for_desktop_renderer',{'return_value':True}),
                ('codex_relay.switch.codex_env.open_thread_deeplink',{'return_value':None}),
            ]:stack.enter_context(patch(target,**kwargs))
            from codex_relay.desktop_continue import runtime_task
            def lookup(s,tid):
                if tid==OLD:raise ThreadUnavailable('no-client-found')
                return runtime_task(s,tid)
            stack.enter_context(patch('codex_relay.desktop_continue.runtime_task',side_effect=lookup))
            stack.enter_context(contextlib.redirect_stdout(output))
            self.assertEqual(_auto_once(store,args),0)
        result=json.loads(output.getvalue())
        self.assertEqual(result['action'],'switched');self.assertTrue(result['continued'])
        self.assertEqual(result['continued_turn_id'],'confirmed-new-turn')
        self.assertEqual(app.calls,[('interrupt',THREAD,'turn1'),('resume',THREAD,'continue original work')])
        self.assertEqual(read_auth(store.paths.auth_path)['tokens']['account_id'],'acct-b')
