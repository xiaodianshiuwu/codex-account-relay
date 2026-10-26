"""Exercise the frozen daemon through a synthetic desktop and app-server.

No real authentication, app lifecycle control or model calls. Usage:
python tools/smoke_bundle.py /path/to/bundled/codex-relay
"""
from __future__ import annotations
import contextlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import socket
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import time

THREAD = '11111111-1111-4111-8111-111111111111'
ABANDONED = '22222222-2222-4222-8222-222222222222'
STATE = {'id': THREAD, 'hostId':'local', 'ephemeral':False, 'resumeState':'resumed',
         'requests':[], 'queuedFollowUps':[], 'currentPermissions':{'sandbox':'workspace-write'},
         'latestModel':'fixture-model','cwd':'/synthetic/project',
         'threadRuntimeStatus':{'type':'active','activeFlags':[]},
         'turns':[{'turnId':'fixture-turn','status':'inProgress','error':None,'params':{}}]}

def main(binary):
    binary = str(Path(binary).resolve())
    with tempfile.TemporaryDirectory(prefix='relay-bundle-', dir='/tmp') as tmp:
        root=Path(tmp); home=root/'home'; state=root/'guard'; relay=root/'relay'; app=root/'Applications/ChatGPT.app'
        (home/'ipc').mkdir(parents=True,mode=0o700)
        (app/'Contents/Resources').mkdir(parents=True)
        (app/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'com.openai.codex','CFBundleShortVersionString':'999','CFBundleVersion':'1'}))
        # A real synthetic process establishes a desktop generation for the
        # frozen detector. Only this exact child PID is ever stopped.
        # Keep the synthetic main outside the app bundle: macOS protects a
        # running bundle and can block its fixture script from executing.
        (root/'synthetic/MacOS').mkdir(parents=True)
        fake_main=root/'synthetic/MacOS/ChatGPT'
        shutil.copy('/bin/sleep',fake_main)
        with contextlib.closing(sqlite3.connect(home/'state_5.sqlite')) as db, db:
            db.execute('CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT, created_at INTEGER, updated_at INTEGER, cwd TEXT, title TEXT, source TEXT, tokens_used INTEGER, archived INTEGER)')
            for tid in (THREAD,ABANDONED):
                db.execute('INSERT INTO threads VALUES (?, ?, 1, 1, ?, ?, ?, 0, 0)',(tid,'sessions/'+tid+'.jsonl','/fixture','fixture','vscode'))
        with contextlib.closing(sqlite3.connect(home/'thread_history_1.sqlite')) as db, db:
            db.execute('CREATE TABLE thread_turns (thread_id TEXT, turn_id TEXT, status TEXT, started_at INTEGER, completed_at INTEGER)')
            for tid in (THREAD,ABANDONED):
                db.execute('INSERT INTO thread_turns VALUES (?, ?, ?, 1, NULL)',(tid,'fixture-turn','inProgress'))
        cli=app/'Contents/Resources/codex'
        cli.write_text('''#!/usr/bin/env python3
import json,sys,time
for line in sys.stdin:
    request=json.loads(line)
    if 'id' not in request:continue
    method=request.get('method')
    if method=='initialize':result={}
    elif method=='account/rateLimits/read':
        result={'rateLimitsByLimitId':{'codex':{'primary':{'usedPercent':10,'resetsAt':int(time.time())+3600,'windowDurationMins':300}}}}
    else:raise RuntimeError('unexpected fake RPC: '+str(method))
    print(json.dumps({'id':request['id'],'result':result}),flush=True)
''');cli.chmod(0o755)
        listener=socket.socket(socket.AF_UNIX); listener.bind(str(home/'ipc/ipc.sock'));os.chmod(home/'ipc/ipc.sock',0o600);listener.listen();listener.settimeout(.2)
        stop=threading.Event(); errors=[]; mutation=[]
        def exact(sock,n):
            result=b''
            while len(result)<n:
                chunk=sock.recv(n-len(result))
                if not chunk:raise EOFError()
                result+=chunk
            return result
        def send(sock,msg):
            data=json.dumps(msg).encode();sock.sendall(struct.pack('<I',len(data))+data)
        def connection(sock):
            with sock:
                try:
                    while not stop.is_set():
                        msg=json.loads(exact(sock,struct.unpack('<I',exact(sock,4))[0]))
                        if msg['type']=='broadcast':
                            if msg['params'].get('following'):
                                send(sock,{'type':'broadcast','method':'thread-stream-state-changed','sourceClientId':'owner','version':11,
                                    'params':{'conversationId':THREAD,'hostId':'local','change':{'type':'snapshot','revision':1,'conversationState':STATE}}})
                            continue
                        method=msg['method']
                        if method not in ('initialize','thread-owner-discovery'):
                            mutation.append(method);raise RuntimeError('unexpected desktop mutation')
                        if method=='thread-owner-discovery' and msg['params']['conversationId']==ABANDONED:
                            send(sock,{'type':'response','requestId':msg['requestId'],'method':method,
                                       'resultType':'error','error':'no-client-found'})
                            continue
                        send(sock,{'type':'response','requestId':msg['requestId'],'method':method,'resultType':'success','handledByClientId':'owner',
                                   'result':{'clientId':'guard'} if method=='initialize' else {}})
                except EOFError:pass
                except Exception as exc:errors.append(repr(exc))
        def serve():
            while not stop.is_set():
                try:sock,_=listener.accept()
                except socket.timeout:continue
                threading.Thread(target=connection,args=(sock,),daemon=True).start()
        worker=threading.Thread(target=serve,daemon=True);worker.start()
        env=dict(os.environ,HOME=str(root),CODEX_HOME=str(home),CODEX_RELAY_HOME=str(relay),CODEX_RELAY_GUARD_HOME=str(state),CODEX_RELAY_TEST_MODE='1')
        def call(*args,ok=True):
            result=subprocess.run([binary,'guard','--app',str(app),'--home',str(home),'--state-dir',str(state),*args],env=env,capture_output=True,text=True,timeout=20)
            if ok and result.returncode:
                logs = '\n'.join(p.read_text() for p in state.glob('*.log'))
                raise AssertionError(result.stderr + '\nFixture worker log:\n' + logs)
            return result
        config=root/'settings.json';prompt='fixture continuation '*300
        config.write_text(json.dumps({'threshold':5,'resumeRemaining':10,'interval':.1,'continuation':prompt}))
        fake_desktop=subprocess.Popen([str(fake_main),'180'])
        try:
            def activity():
                result=subprocess.run([binary,'activity','--json'],env=env,capture_output=True,text=True,timeout=20)
                assert result.returncode==0,result.stderr
                return json.loads(result.stdout)
            live=activity()
            assert [r['thread_id'] for r in live['active']]==[THREAD],live
            assert [r['thread_id'] for r in live['ignored_history']]==[ABANDONED],live
            STATE['turns'][0]['status']='interrupted'
            STATE['threadRuntimeStatus']={'type':'idle'}
            terminal=activity()
            assert terminal['idle'] and len(terminal['ignored_history'])==2,terminal
            STATE['turns'][0]['status']='inProgress'
            STATE['threadRuntimeStatus']={'type':'active','activeFlags':[]}
            call('settings','--file',str(config))
            started=json.loads(call('background',THREAD).stdout)
            time.sleep(.4)  # parent has exited; frozen worker must own its runtime
            records=json.loads(call('status').stdout)
            assert records[0]['monitoring'] is True
            assert records[0]['configuration']['continuation']==prompt.strip()
            assert call('background',THREAD,ok=False).returncode!=0
            call('stop',THREAD)
            deadline=time.monotonic()+6
            while time.monotonic()<deadline:
                records=json.loads(call('status').stdout)
                if not records[0]['processAlive']:break
                time.sleep(.1)
            assert not records[0]['processAlive'], {'phase':records[0].get('phase'),'lastError':records[0].get('lastError'),'log':'\n'.join(p.read_text() for p in state.glob('*.log'))}
            assert not mutation,mutation
            assert not errors,errors
            print(json.dumps({'frozenBackgroundStart':True,'survivesParentExit':True,'customPromptPreserved':True,'duplicateRejected':True,
                              'stopConfirmed':True,'abandonedHistoryExcluded':True,'liveTurnBlocked':True,
                              'interruptedTurnBecomesIdle':True,'desktopMutations':0,'realCredentialsUsed':False}))
        finally:
            try:call('stop',THREAD)
            finally:
                stop.set();worker.join(timeout=1);listener.close()
                fake_desktop.terminate();fake_desktop.wait(timeout=3)

if __name__=='__main__':main(sys.argv[1])
