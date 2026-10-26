"""Exact original desktop chat continuation with durable operation receipts.

This module never launches or quits an app. Account switching is the caller's
separately authorized transaction. No `codex exec` or default execution settings.
"""
from __future__ import annotations
import json
import time
import uuid
from pathlib import Path
from .util import RelayError, atomic_write_json
from desktop_guard.desktop import Desktop, NotDispatched, ConnectionUnavailable
from desktop_guard.policy import task, identity


def controller(store):
    from .codex_env import discover_desktop
    from .guard_cli import local_metadata
    from argparse import Namespace
    app = discover_desktop()
    if not app.path: raise RelayError("Desktop app unavailable")
    args = Namespace(app=app.path, home=store.paths.codex_home)
    return Desktop(store.paths.codex_home, str(app.path),
                   metadata_reader=lambda tid, budget: local_metadata(args, tid, budget))


def runtime_task(store, thread_id):
    with controller(store) as app:
        state = app.snapshot(thread_id)
        return task(state), state


def prepare_handoff(store, thread_id, execute=False, dispatch_guard=None):
    with controller(store) as app:
        state = app.snapshot(thread_id)
        current = task(state)
        if current.kind not in ('running', 'quota_error'):
            raise RelayError('Original chat is not resumable: ' + current.reason)
        context = {'thread_id': thread_id, 'turn_id': current.turn_id,
                   'identity': identity(state), 'interrupted': False}
        path = store.paths.relay_home / 'continuations' / (thread_id + '.json')
        if path.exists():
            previous = json.loads(path.read_text())
            if previous.get('turn_id') == current.turn_id and previous.get('status') in ('interrupt-intent', 'resume-intent', 'confirmed'):
                raise RelayError('This original turn already has an operation receipt; inspect it before retrying')
        if not execute: return context
        if current.kind == 'running':
            context['status'] = 'interrupt-intent'
            atomic_write_json(path, context)
            try:
                result = app.interrupt(thread_id, current.turn_id,
                    lambda fresh: task(fresh).kind == 'running' and identity(fresh) == context['identity'], dispatch_guard)
            except NotDispatched:
                atomic_write_json(path, dict(context, status='not-sent'))
                raise
            context.update(status='interrupted', interrupted=True)
            atomic_write_json(path, context)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                fresh = app.snapshot(thread_id)
                if task(fresh).turn_id != current.turn_id: raise RelayError('Original turn changed during interrupt')
                if task(fresh).kind == 'interrupted': break
                time.sleep(.2)
            else: raise RelayError('Original desktop interrupt is not yet visible; account unchanged')
        else:
            context['status'] = 'prepared'
            atomic_write_json(path, context)
        return context


def continue_desktop_thread(store, context, prompt):
    thread_id = context['thread_id']
    path = store.paths.relay_home / 'continuations' / (thread_id + '.json')
    record = json.loads(path.read_text())
    if record.get('turn_id') != context['turn_id'] or record.get('status') in ('resume-intent', 'confirmed'):
        raise RelayError('Continuation has an existing or uncertain receipt; not sent again')
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        app = controller(store)
        try:
            app.__enter__()
            state = app.snapshot(thread_id)
        except (ConnectionUnavailable, OSError, TimeoutError):
            app.__exit__(None, None, None)
            time.sleep(.3)
            continue
        try:
            # Only reads are retried while the restarted renderer loads.
            # Once an intent is written, a lost send/receipt is never retried.
            current = task(state)
            if current.kind == 'connecting' or (current.kind == 'running' and current.turn_id == context['turn_id']):
                time.sleep(.3)
                continue
            def valid(fresh):
                current = task(fresh)
                return (current.kind in ('quota_error', 'interrupted') and
                        current.turn_id == context['turn_id'] and identity(fresh) == context['identity'])
            if not valid(state): raise RelayError('Original chat or execution settings changed; continuation not sent')
            message_id = str(uuid.uuid4())
            atomic_write_json(path, dict(context, status='resume-intent', message_id=message_id))
            app.continuation = prompt
            try:
                turn_id = app.resume(thread_id, message_id, valid)
            except NotDispatched:
                atomic_write_json(path, dict(context, status='not-sent', message_id=message_id))
                raise
            atomic_write_json(path, dict(context, status='confirmed', message_id=message_id, result_turn_id=turn_id))
            return turn_id
        finally:
            app.__exit__(None, None, None)
    raise RelayError('Original desktop chat did not finish loading within 60 seconds; account switched but continuation not sent')


def switch_and_continue(store, options, prompt, progress=None):
    from dataclasses import replace
    from .switch import switch, preflight_processes
    from .usage import refresh_usage, idle_check, usage_known, max_used_percent
    if options.mode != 'desktop' or options.thread in (None, 'none', 'last') or not prompt.strip():
        raise RelayError('Continuation requires an exact local chat and a nonempty prompt')
    target = store.resolve(options.target)
    if options.verify:
        usage = refresh_usage(store, target)
        if not usage_known(usage) or max_used_percent(usage) >= 100:
            raise RelayError('Target account quota is unavailable; original chat unchanged')
    context = prepare_handoff(store, options.thread)
    if not idle_check(store, ignore_thread_id=options.thread)['idle']:
        raise RelayError('Another chat is still running; original chat unchanged')
    preflight_processes('desktop', options.force)
    if options.dry_run:
        return {'ok':True, 'changed':False, 'reason':'dry-run', 'mode':'desktop',
                'thread_id':options.thread, 'to':{'alias':target['alias']}, 'continued':False}
    if not options.allow_chatgpt_restart:
        raise RelayError('ChatGPT restart permission is required; original chat unchanged')
    context = prepare_handoff(store, options.thread, execute=True)
    result = switch(store, replace(options, verify=False), progress=progress)
    try:
        result['continued_turn_id'] = continue_desktop_thread(store, context, prompt)
        result['continued'] = True
    except (RuntimeError, OSError, TimeoutError, ValueError) as exc:
        result.update(continued=False, continue_error=str(exc))
    return result
