import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from codex_relay.desktop_continue import prepare_handoff, continue_desktop_thread
from codex_relay.store import Store, RelayPaths
from codex_relay.util import RelayError
from desktop_guard.desktop import NotDispatched
from tests.guard.test_guard import state, THREAD

class FakeApp:
    def __init__(self, initial): self.state = initial; self.calls = []; self.failure = None
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def snapshot(self, tid): return copy.deepcopy(self.state)
    def interrupt(self, tid, turn, valid, dispatch_guard=None):
        assert valid(self.state)
        self.calls.append(('interrupt', tid, turn)); self.state = state('interrupted', tid=turn)
        return turn
    def resume(self, tid, message, valid):
        assert valid(self.state)
        self.calls.append(('resume', tid, self.continuation))
        if self.failure: raise self.failure
        return 'confirmed-new-turn'

class DesktopContinuationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(RelayPaths(Path(self.tmp.name)/'relay', Path(self.tmp.name)/'codex'))
        self.app = FakeApp(state('failed', error={'codexErrorInfo':'usageLimitExceeded'}))
        self.mock = patch('codex_relay.desktop_continue.controller', return_value=self.app)
        self.mock.start(); self.addCleanup(self.mock.stop)
    def test_dry_preflight_never_interrupts_or_writes_receipt(self):
        self.app.state = state()
        context = prepare_handoff(self.store, THREAD)
        self.assertEqual(context['turn_id'], 'turn1'); self.assertEqual(self.app.calls, [])
        self.assertFalse(self.store.paths.relay_home.exists())
    def test_running_original_turn_interrupts_then_exact_chat_receives_prompt(self):
        self.app.state = state()
        context = prepare_handoff(self.store, THREAD, execute=True)
        self.assertEqual(continue_desktop_thread(self.store, context, '继续原任务'), 'confirmed-new-turn')
        self.assertEqual(self.app.calls, [('interrupt',THREAD,'turn1'), ('resume',THREAD,'继续原任务')])
        record=json.loads((self.store.paths.relay_home/'continuations'/f'{THREAD}.json').read_text())
        self.assertEqual(record['status'],'confirmed')
        self.assertEqual(record['result_turn_id'],'confirmed-new-turn')
        self.assertNotIn('继续原任务',json.dumps(record))
    def test_unknown_receipt_survives_crash_and_is_never_resent(self):
        context=prepare_handoff(self.store,THREAD,execute=True)
        self.app.failure=TimeoutError('lost receipt')
        with self.assertRaises(TimeoutError):continue_desktop_thread(self.store,context,'continue')
        with self.assertRaises(RelayError):continue_desktop_thread(self.store,context,'continue')
        with self.assertRaises(RelayError):prepare_handoff(self.store,THREAD,execute=True)
        self.assertEqual(len(self.app.calls),1)
    def test_changed_model_directory_turn_or_permissions_never_dispatch(self):
        for key,value in [('latestModel','other'),('cwd','/other'),('currentPermissions',{'sandbox':'other'})]:
            with self.subTest(key=key):
                self.app.state=state('failed', error={'codexErrorInfo':'usageLimitExceeded'})
                context=prepare_handoff(self.store,THREAD,execute=True)
                self.app.state[key]=value
                with self.assertRaises(RelayError):continue_desktop_thread(self.store,context,'continue')
        self.assertEqual(self.app.calls,[])
    def test_completed_or_user_input_chat_not_automatically_restarted(self):
        self.app.state=state('completed')
        with self.assertRaises(RelayError):prepare_handoff(self.store,THREAD,execute=True)
        self.app.state=state();self.app.state['requests']=[{'approval':True}]
        with self.assertRaises(RelayError):prepare_handoff(self.store,THREAD,execute=True)
        self.app.state=state('interrupted')
        with self.assertRaises(RelayError):prepare_handoff(self.store,THREAD,execute=True)
        self.assertEqual(self.app.calls,[])
    def test_explicit_manual_continuation_can_resume_a_user_interrupted_chat(self):
        self.app.state=state('interrupted')
        context=prepare_handoff(self.store,THREAD,execute=True,allow_interrupted=True)
        self.assertEqual(continue_desktop_thread(self.store,context,'explicit manual instruction'),'confirmed-new-turn')
        self.assertEqual(self.app.calls,[('resume',THREAD,'explicit manual instruction')])

    def test_certain_not_sent_result_does_not_record_false_success(self):
        context=prepare_handoff(self.store,THREAD,execute=True)
        self.app.failure=NotDispatched('changed before send')
        with self.assertRaises(NotDispatched):continue_desktop_thread(self.store,context,'continue')
        record=json.loads((self.store.paths.relay_home/'continuations'/f'{THREAD}.json').read_text())
        self.assertEqual(record['status'],'not-sent')

class ManualRelayTests(unittest.TestCase):
    def test_dry_run_never_interrupts_switches_or_sends(self):
        from codex_relay.desktop_continue import switch_and_continue
        from codex_relay.switch import SwitchOptions
        from tests.helpers import new_store, make_auth
        store=new_store();store.add_profile('target',make_auth('acct-target'))
        with patch('codex_relay.desktop_continue.prepare_handoff',return_value={'thread_id':THREAD}) as prepare, patch('codex_relay.switch.preflight_processes',return_value=[]), patch('codex_relay.usage.idle_check',return_value={'idle':True}), patch('codex_relay.switch.switch') as switch, patch('codex_relay.desktop_continue.continue_desktop_thread') as send:
            result=switch_and_continue(store,SwitchOptions(target='target',thread=THREAD,verify=False,dry_run=True),'continue')
            self.assertEqual(result['reason'],'dry-run')
            self.assertEqual(prepare.call_count,1);switch.assert_not_called();send.assert_not_called()
    def test_manual_idle_confirmation_reaches_continuation_without_enabling_broad_force(self):
        from codex_relay.desktop_continue import switch_and_continue
        from codex_relay.switch import SwitchOptions
        from tests.helpers import new_store, make_auth
        store=new_store();store.add_profile('target',make_auth('acct-target'))
        with patch('codex_relay.desktop_continue.prepare_handoff',return_value={'thread_id':THREAD}), patch('codex_relay.switch.preflight_processes',return_value=[]), patch('codex_relay.usage.idle_check',return_value={'idle':False,'active':[]}), patch('codex_relay.switch.switch',return_value={'changed':True,'ok':True}) as change, patch('codex_relay.desktop_continue.continue_desktop_thread',return_value='new-turn'):
            options=SwitchOptions(target='target',thread=THREAD,verify=False,allow_chatgpt_restart=True)
            with self.assertRaises(RelayError):switch_and_continue(store,options,'continue')
            change.assert_not_called()
            options.confirmed_idle=True
            result=switch_and_continue(store,options,'continue')
            self.assertTrue(result['continued'])
            self.assertTrue(change.call_args.args[1].confirmed_idle)
            self.assertFalse(change.call_args.args[1].force)

    def test_manual_switch_reports_partial_success_when_continuation_fails(self):
        from codex_relay.desktop_continue import switch_and_continue
        from codex_relay.switch import SwitchOptions
        from tests.helpers import new_store, make_auth
        store=new_store();store.add_profile('target',make_auth('acct-target'))
        with patch('codex_relay.desktop_continue.prepare_handoff',return_value={'thread_id':THREAD}), patch('codex_relay.switch.preflight_processes',return_value=[]), patch('codex_relay.usage.idle_check',return_value={'idle':True}), patch('codex_relay.switch.switch',return_value={'changed':True,'ok':True}), patch('codex_relay.desktop_continue.continue_desktop_thread',side_effect=TimeoutError('receipt unknown')):
            result=switch_and_continue(store,SwitchOptions(target='target',thread=THREAD,verify=False,allow_chatgpt_restart=True),'continue')
            self.assertTrue(result['changed']);self.assertFalse(result['continued']);self.assertIn('unknown',result['continue_error'])
