import copy
import json
import tempfile
import time
import unittest
from unittest.mock import patch

from desktop_guard.desktop import AppError, NotDispatched, ConnectionUnavailable
from desktop_guard.engine import Engine
from desktop_guard.policy import task, identity
from desktop_guard.remote import connection_key, RemoteReadOnlyServer
from desktop_guard.remote_control import RemoteController
from desktop_guard.store import Store, statuses
from .test_guard import THREAD, state, usage, FakeDesktop
from .test_remote import HOST, DESCRIPTOR


def remote_state(status='inProgress', turn='turn1'):
    s = state(status, turn)
    s.update(hostId=HOST, canAcceptDirectInput=True, remoteConnectionKey='host-key',
             remoteBinding={'sessionId': THREAD})
    return s


class RemotePolicyTests(unittest.TestCase):
    def test_same_uuid_locks_cancellation_and_records_are_separate_per_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            local, remote = Store(tmp, THREAD), Store(tmp, THREAD, HOST)
            local.acquire();remote.acquire()
            try:
                local.begin();remote.begin();remote.cancel()
                self.assertTrue(local.live());self.assertFalse(remote.live())
                records = statuses(tmp)
                self.assertEqual({r['hostId'] for r in records}, {'local', HOST})
                self.assertNotEqual(local.path, remote.path)
                duplicate = Store(tmp, THREAD, HOST)
                with self.assertRaises(RuntimeError):duplicate.acquire()
            finally:local.close();remote.close()

    def test_host_change_and_missing_direct_input_refuse_action(self):
        s = remote_state()
        self.assertEqual(task(s, HOST).kind, 'running')
        self.assertEqual(task(s).kind, 'blocked')
        for value in (False, None):
            s['canAcceptDirectInput'] = value
            self.assertEqual(task(s, HOST).kind, 'blocked')

    def test_remote_binding_changes_are_part_of_waiting_identity(self):
        s = remote_state()
        original = identity(s)
        s['remoteBinding']['sessionId'] = 'different-session'
        self.assertNotEqual(original, identity(s))

    def test_remote_disconnect_retains_waiting_monitor_without_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp, THREAD, HOST);store.begin()
            factory = unittest.mock.MagicMock()
            factory.return_value.__enter__.side_effect = ConnectionUnavailable()
            engine = Engine(store, factory, lambda: usage(95))
            self.assertTrue(engine.step());self.assertTrue(store.live())
            self.assertEqual(store.data['attempts'], {})

    def test_changed_remote_connection_never_interrupts_matching_uuid_on_new_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp, THREAD, HOST);store.begin()
            env = {'state': remote_state(), 'interrupts': 0, 'resumes': 0}
            store.save(remoteBinding={'hostKey': 'old-host', 'sessionId': THREAD})
            engine = Engine(store, lambda: FakeDesktop(env), lambda: usage(95), clock=lambda:1000)
            self.assertFalse(engine.step());self.assertEqual(env['interrupts'], 0)

    def test_remote_approval_queue_and_goal_pause_are_not_auto_resumed(self):
        for field, value in [('requests', [True]), ('queuedFollowUps', [True]),
                             ('threadGoal', {'status': 'paused'})]:
            s = remote_state('interrupted');s[field] = value
            self.assertEqual(task(s, HOST).kind, 'blocked')


class RemoteAdapterTests(unittest.TestCase):
    def controller(self):
        return RemoteController(DESCRIPTOR)

    def test_connection_config_change_refuses_even_before_ssh(self):
        controller = RemoteController(DESCRIPTOR, expected_key='old-key')
        with patch.object(RemoteReadOnlyServer, '__enter__') as enter:
            with self.assertRaises(AppError):controller.__enter__()
            enter.assert_not_called()

    def test_new_daemon_version_does_not_block_connection(self):
        controller = self.controller();controller.initialization={'userAgent':'codex-tui/999.0.0'}
        with patch.object(RemoteReadOnlyServer, '__enter__'), patch.object(controller, '__exit__'):
            self.assertIs(controller.__enter__(), controller)

    def test_resume_uses_original_loaded_thread_and_does_not_override_execution_settings(self):
        controller = self.controller()
        s = remote_state('interrupted');s['turns'][0]['params']={'rootTurnId':'root-turn'}
        with patch.object(controller, '_validate', return_value=s), patch.object(controller, '_request', return_value={'turn':{'id':'next-turn'}}) as request:
            self.assertEqual(controller.resume(THREAD, 'message-id', lambda s:True), 'next-turn')
            method, params = request.call_args.args
            self.assertEqual(method,'turn/start');self.assertEqual(params['threadId'],THREAD)
            self.assertEqual(params['clientUserMessageId'],'message-id')
            for key in ['model','cwd','permissions','approvalPolicy','sandboxPolicy','collaborationMode']:
                self.assertNotIn(key,params)
            self.assertEqual(params['parentTurnId'],'turn1');self.assertEqual(params['rootTurnId'],'root-turn')

    def test_interrupt_requires_matching_turn_to_become_interrupted(self):
        controller = self.controller()
        with (patch.object(controller,'_validate'),patch.object(controller,'_request',return_value={}) as request,
              patch.object(controller,'snapshot',return_value=remote_state('interrupted'))):
            self.assertEqual(controller.interrupt(THREAD,'turn1',lambda s:True),'turn1')
            request.assert_called_once_with('turn/interrupt',{'threadId':THREAD,'turnId':'turn1'})

    def test_empty_dispatch_ack_does_not_own_a_turn_that_completed_normally(self):
        controller = self.controller()
        with (patch.object(controller,'_validate'),patch.object(controller,'_request',return_value={}),
              patch.object(controller,'snapshot',return_value=remote_state('completed'))):
            with self.assertRaises(AppError):controller.interrupt(THREAD,'turn1',lambda s:True)

    def test_cancel_before_remote_send_never_writes_mutation(self):
        controller = self.controller()
        with (patch.object(controller,'snapshot',return_value=remote_state()),patch.object(controller,'_drain'),
              patch.object(controller,'_request') as request):
            with self.assertRaises(NotDispatched):controller.interrupt(THREAD,'turn1',lambda s:True,lambda:False)
            request.assert_not_called()

    def test_fresh_remote_event_before_send_refuses_mutation(self):
        controller = self.controller()
        def changed():controller.generation += 1
        with (patch.object(controller,'snapshot',return_value=remote_state()),patch.object(controller,'_drain',side_effect=changed),
              patch.object(controller,'_request') as request):
            with self.assertRaises(NotDispatched):controller.interrupt(THREAD,'turn1',lambda s:True,lambda:True)
            request.assert_not_called()

    def test_approval_request_is_observed_without_sending_a_response(self):
        controller = self.controller();controller.thread_id=THREAD
        controller._observe({'id':100,'method':'item/commandExecution/requestApproval','params':{'threadId':THREAD}})
        self.assertTrue(controller.pending_request)
        self.assertEqual(controller.generation,1)

    def test_snapshot_uses_summary_only_and_checks_latest_tail_twice(self):
        controller = self.controller()
        thread={'id':THREAD,'sessionId':THREAD,'ephemeral':False,'model':'original-model',
                'cwd':'/synthetic','canAcceptDirectInput':True,'status':{'type':'active','activeFlags':[]}}
        tail={'data':[{'id':'turn1','status':'inProgress','error':None,'rootTurnId':'turn1'}]}
        with patch.object(controller,'query',side_effect=[{'thread':thread},tail,{'goal':None},
                                                       {'data':[],'nextCursor':None},{'thread':thread},tail]) as query:
            s=controller.snapshot(THREAD)
            self.assertEqual(task(s,HOST).kind,'running')
            self.assertEqual(s['remoteConnectionKey'],connection_key(DESCRIPTOR))
            self.assertEqual(sum(c.args[0]=='thread/turns/list' for c in query.call_args_list),2)
            for call in query.call_args_list:
                if call.args[0]=='thread/read':self.assertFalse(call.args[1]['includeTurns'])

    def test_latest_turn_changed_during_snapshot_cannot_authorize_a_send(self):
        controller=self.controller()
        thread={'id':THREAD,'status':{'type':'active','activeFlags':[]}}
        with patch.object(controller,'query',side_effect=[{'thread':thread},{'data':[{'id':'old'}]},
                    {'goal':None},{'data':[]},{'thread':thread},{'data':[{'id':'new'}]}]):
            with self.assertRaises(ConnectionUnavailable):controller.snapshot(THREAD)

    def test_quota_wait_context_exit_keeps_subscription_until_monitor_ends(self):
        controller=RemoteController(DESCRIPTOR,keep_alive=True)
        with patch.object(controller,'close') as close:
            controller.__exit__(None,None,None);close.assert_not_called()
            controller.__exit__(ConnectionUnavailable,ConnectionUnavailable(),None)
            close.assert_called_once()

    def test_loaded_subscription_reads_live_permissions_without_overrides(self):
        controller=RemoteController(DESCRIPTOR,keep_alive=True)
        thread={'id':THREAD,'sessionId':THREAD,'ephemeral':False,'model':'original-model',
                'cwd':'/synthetic','canAcceptDirectInput':True,'status':{'type':'active','activeFlags':[]}}
        tail={'data':[{'id':'turn1','status':'inProgress','error':None,'rootTurnId':'turn1'}]}
        loaded={'thread':thread,'model':'original-model','cwd':'/synthetic','approvalPolicy':'on-request',
                'sandbox':{'type':'readOnly','networkAccess':False}}
        with (patch.object(controller,'query',side_effect=[{'thread':thread},tail,{'goal':None},
                {'data':[],'nextCursor':None},{'thread':thread},tail]),
              patch.object(controller,'_request',return_value=loaded) as request):
            snapshot=controller.snapshot(THREAD)
            request.assert_called_once_with('thread/resume',{'threadId':THREAD,'excludeTurns':True})
            self.assertEqual(snapshot['latestThreadSettings']['sandbox']['type'],'readOnly')
            self.assertTrue(controller.subscribed)

    def test_unloaded_thread_is_never_reopened_with_cli_default_permissions(self):
        controller=RemoteController(DESCRIPTOR,keep_alive=True)
        thread={'id':THREAD,'model':'original-model','status':{'type':'notLoaded'}}
        tail={'data':[{'id':'turn1','status':'interrupted'}]}
        with (patch.object(controller,'query',side_effect=[{'thread':thread},tail,{'goal':None},
                {'data':[]},{'thread':thread},tail]),patch.object(controller,'_request') as request):
            snapshot=controller.snapshot(THREAD)
            request.assert_not_called()
            self.assertEqual(snapshot['resumeState'],'resuming')
