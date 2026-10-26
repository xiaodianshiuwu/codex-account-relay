#!/usr/bin/env python3
"""CLI and double-click workflow. No UI clicking, external packages, or model runs."""
import argparse
from datetime import datetime
import json
import math
import os
from pathlib import Path
import plistlib
import signal
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from desktop_guard.desktop import Desktop, check_version
from desktop_guard.engine import Engine
from desktop_guard.policy import task, quota
from desktop_guard.rpc import ReadOnlyServer
from desktop_guard.remote import hosts, selected_host, RemoteReadOnlyServer, daemon_version, connection_key
from desktop_guard.remote_control import RemoteController
from desktop_guard.store import Store, statuses, running
from desktop_guard.diagnostics import diagnostic
from desktop_guard import settings
from desktop_guard.forecast import forecast
import tempfile

ENTRY = Path(sys.executable).resolve() if getattr(sys, 'frozen', False) else Path(__file__).resolve()
ROOT = ENTRY.parent

def backend_command():
    return [str(ENTRY), 'guard'] if getattr(sys, 'frozen', False) else [sys.executable, '-m', 'codex_relay.cli', 'guard']
REQUEST_TIME_NS = time.time_ns()


def child_environment():
    env = dict(os.environ)
    if not getattr(sys, 'frozen', False):
        env['PYTHONPATH'] = str(Path(__file__).resolve().parent.parent) + os.pathsep + env.get('PYTHONPATH', '')
    else:
        # The daemon must unpack its own runtime after the launching process exits.
        env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    return env


def default_state_dir():
    return Path(os.environ.get('CODEX_RELAY_GUARD_HOME') or
                (str(Path(os.environ['CODEX_RELAY_HOME']) / 'guard') if os.environ.get('CODEX_RELAY_HOME') else
                 str(Path.home() / 'Library/Application Support/CodexDesktopGuard')))


def default_app():
    from .codex_env import discover_desktop
    return discover_desktop().path or Path('/Applications/ChatGPT.app')


def stamp(value=None):
    return datetime.fromtimestamp(time.time() if value is None else value).astimezone().strftime('%Y-%m-%d %H:%M:%S')


def emit(value):
    from .i18n import Translator
    translator = Translator()
    def translated(item):
        if isinstance(item, list): return [translated(v) for v in item]
        if not isinstance(item, dict): return item
        result = dict(item)
        for key, child in item.items():
            if key in ('reason', 'quotaReason') and isinstance(child, str): result[key] = translator.external(child)
            elif key == 'warnings' and isinstance(child, list): result[key] = [translator.external(v) for v in child]
            elif isinstance(child, (list, dict)): result[key] = translated(child)
        return result
    print(json.dumps(translated(value), ensure_ascii=False, indent=2), flush=True)


def uuid_arg(value):
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise argparse.ArgumentTypeError('需要完整的原聊天 UUID，不能用标题或 --last') from exc


def binary_for(app):
    for relative in ('Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex',
                     'Contents/Resources/codex'):
        path = app / relative
        if path.is_file() and os.access(path, os.X_OK):
            return path
    raise RuntimeError('所选 App 内找不到 Codex 可执行文件')


def windows(q):
    return [{'windowMinutes': w['windowDurationMins'], 'remainingPercent': w['remaining'],
             'resetsAtLocal': stamp(w['resetsAt'])} for w in q.windows]


def server(args):
    if getattr(args, 'host', 'local') != 'local':
        return RemoteReadOnlyServer(selected_host(args.home, args.host))
    return ReadOnlyServer(binary_for(args.app), args.home)


def local_metadata(args, thread_id, budget):
    reader = ReadOnlyServer(binary_for(args.app), args.home, timeout=6)
    with reader.bounded(budget), reader as rpc:
        return rpc.query('thread/read', {'threadId': thread_id, 'includeTurns': False}).get('thread')


def desktop(args):
    if getattr(args, 'host', 'local') != 'local':
        return RemoteController(selected_host(args.home, args.host), expected_key=getattr(args, 'connection_key', None))
    return Desktop(args.home, str(args.app), metadata_reader=lambda tid, budget: local_metadata(args, tid, budget))


def query_threads(rpc, limit=0):
    threads, seen, cursor = [], set(), None
    while True:
        params = {'limit': min(100, limit - len(threads)) if limit > 0 else 100, 'archived': False}
        if cursor: params['cursor'] = cursor
        result = rpc.query('thread/list', params)
        data = result.get('data')
        if not isinstance(data, list): raise RuntimeError('Invalid thread list response')
        threads.extend(data)
        cursor = result.get('nextCursor')
        if not cursor or limit > 0 and len(threads) >= limit: return threads
        if cursor in seen: raise RuntimeError('Thread list pagination did not advance')
        seen.add(cursor)


def list_threads(args):
    with server(args) as rpc:
        result = {'data': query_threads(rpc, getattr(args, 'limit', 30))}
    return [{'id': t['id'], 'name': t.get('name') or t.get('preview', '')[:60],
             'cwd': t.get('cwd'), 'source': t.get('source'), 'hostId': args.host} for t in result.get('data', [])]


def check(args, thread_id):
    check_version(args.app)
    controller = desktop(args)
    with controller.bounded(16), controller as app:
        state = app.snapshot(thread_id)
        if state.get('id') != thread_id:
            raise RuntimeError('返回了不匹配的聊天，停止读取')
        t = task(state, args.host)
    return {'threadId': thread_id, 'hostId': args.host, 'kind': t.kind, 'turnId': t.turn_id, 'reason': t.reason,
            'connectionKey': state.get('remoteConnectionKey'),
            'canMonitor': t.kind in ('running', 'quota_error', 'goal_wait')}


def doctor(args):
    info = plistlib.loads((args.app / 'Contents/Info.plist').read_bytes())
    result = {'app': str(args.app), 'version': info.get('CFBundleShortVersionString'),
              'build': info.get('CFBundleVersion'),
              'compatibility': 'runtime-protocol-validation', 'exactVersionRequired': False}
    check_version(args.app)
    with desktop(args):
        result['desktopIpc'] = 'connected'
    with server(args) as rpc:
        q = quota(rpc.query('account/rateLimits/read'), time.time())
    result.update(quotaKnown=q.known, windows=windows(q), reason=q.reason,
                  modelCalls=0, automaticActionsEnabled=False)
    emit(result)


def preview(args):
    check_version(args.app)
    previous = None
    while True:
        try:
            checked = check(args, args.thread_id)
            with server(args) as rpc:
                q = quota(rpc.query('account/rateLimits/read'), time.time(),
                          args.threshold, args.resume_remaining, args.limit_id)
            result = {'mode': '只读预览', 'kind': checked['kind'], 'reason': checked['reason'],
                      'hostId': args.host,
                      'wouldInterrupt': checked['kind'] == 'running' and q.low and q.known,
                      'quotaKnown': q.known, 'windows': windows(q)}
            if result != previous:
                emit(result)
                previous = result
            if checked['kind'] in ('blocked', 'finished', 'interrupted'):
                return
        except (ConnectionError, FileNotFoundError, TimeoutError):
            print('等待原聊天或网络连接恢复', flush=True)
        time.sleep(args.interval)


def watch(args):
    if not args.execute:
        return preview(args)
    check_version(args.app)
    if args.host != 'local':
        args.connection_key = args.connection_key or connection_key(selected_host(args.home, args.host))
    store = Store(args.state_dir, args.thread_id, args.host)
    store.acquire()
    remote_client = None
    cancelled = False
    def cancel(*unused):
        nonlocal cancelled
        cancelled = True
        # Signal handlers must never acquire the control lock held by begin/save.
        store.cancel_requested = True
    signal.signal(signal.SIGTERM, cancel)
    signal.signal(signal.SIGINT, cancel)
    try:
        store.begin(getattr(args, 'requested_at_ns', REQUEST_TIME_NS))
        factory = lambda: desktop(args)
        if args.host != 'local':
            remote_client = RemoteController(selected_host(args.home, args.host),
                expected_key=args.connection_key, keep_alive=True)
            factory = lambda: remote_client
        engine = Engine(store, factory,
                        lambda: read_quota(args), args.threshold, args.resume_remaining,
                        args.limit_id, args.max_resumes, continuation=getattr(args, 'continuation', settings.DEFAULT_MESSAGE))
        store.save(startupToken=getattr(args, 'startup_token', None), configuration={
            'threshold': args.threshold, 'resumeRemaining': args.resume_remaining, 'interval': args.interval,
            'continuation': getattr(args, 'continuation', settings.DEFAULT_MESSAGE)})
        previous = None
        errors = 0
        print(f'[{stamp()}] 已监控原聊天 {args.thread_id}；小时额度剩余 ≤{args.threshold}% 中断，恢复 ≥{args.resume_remaining}% 续跑；周额度仅显示', flush=True)
        while store.live() and not cancelled:
            try:
                active = engine.step()
                errors = 0
            except Exception as exc:
                errors += 1
                store.save(lastError=diagnostic(exc, 'watch/read'))
                if errors >= 3:
                    store.end('attention', '连续三次状态读取失败，请检查登录、网络和本地记录')
                    active = False
                else:
                    store.save(reason='状态读取失败，稍后重查；不会续跑')
                    active = True
            result = {k: store.data.get(k) for k in ('phase', 'reason', 'resumeCount', 'nextQuotaCheck', 'windows')}
            if result != previous:
                print(f'[{stamp()}] {json.dumps(result, ensure_ascii=False)}', flush=True)
                previous = result
            if not active:
                break
            # 1-second local cancellation checks; no network or model requests here.
            deadline = time.monotonic() + args.interval
            while store.live() and not cancelled and time.monotonic() < deadline:
                time.sleep(min(1, max(0, deadline - time.monotonic())))
        if cancelled:
            store.cancel()
        if cancelled or store.cancel_path.exists():
            uncertain = any(a.get('status') == 'intent' for a in store.data['attempts'].values())
            store.end('uncertain' if uncertain else 'stopped',
                      '监控已取消；已有操作回执未确认，不会重发' if uncertain else '监控已取消；已被接受的操作无法撤回')
    finally:
        if remote_client is not None:
            remote_client.close()
        store.close()


def read_quota(args):
    reader = server(args)
    with reader.bounded(10), reader as rpc:
        return rpc.query('account/rateLimits/read')


def can_enroll(eligibility, record):
    kind = eligibility.get('kind')
    if eligibility.get('canMonitor') is True and kind in ('running', 'quota_error', 'goal_wait'):
        return True
    paused = record.get('paused')
    if record.get('phase') != 'waiting' or not isinstance(paused, dict) or not paused.get('turnId'):
        return False
    if kind not in ('interrupted', 'connecting'):
        return False
    turn = eligibility.get('turnId')
    return turn == paused['turnId'] or (kind == 'connecting' and not turn)


def background(args):
    from .store import Store as AccountStore
    with AccountStore().lock():
        return _background_locked(args)


def _background_locked(args):
    from .auto_config import load_auto_settings
    auto = load_auto_settings()
    if auto['enabled'] and auto['allow_chatgpt_restart']:
        raise RuntimeError('请先关闭自动切号或重启许可，再开始原聊天额度守护')
    eligibility = check(args, args.thread_id)
    store = Store(args.state_dir, args.thread_id, args.host)
    if not can_enroll(eligibility, store.data):
        raise RuntimeError(eligibility['reason'])
    if running(args.state_dir, args.thread_id, args.host):
        raise RuntimeError('该聊天已有监控进程；请先停止后重新启动以应用设置')
    startup_token = str(uuid.uuid4())
    log = store.directory / (args.thread_id + '.log')
    command = backend_command() + ['--app', str(args.app), '--home', str(args.home),
               '--state-dir', str(args.state_dir), '--host', args.host, 'watch', args.thread_id, '--execute',
               '--threshold', str(args.threshold), '--resume-remaining', str(args.resume_remaining),
               '--interval', str(args.interval), '--limit-id', args.limit_id,
               '--max-resumes', str(args.max_resumes), '--requested-at-ns', str(args.requested_at_ns),
               '--startup-token', startup_token]
    if args.host != 'local':
        command += ['--connection-key', eligibility['connectionKey']]
    config_dir = args.state_dir / 'configurations'
    config_path = config_dir / (startup_token + '.json')
    settings.write(config_path, {'threshold': args.threshold, 'resumeRemaining': args.resume_remaining,
        'interval': args.interval, 'continuation': getattr(args, 'continuation', settings.DEFAULT_MESSAGE)})
    command += ['--config-file', str(config_path)]
    try:
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, 'a') as output:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                       start_new_session=True, cwd=Path.home(), env=child_environment())
        deadline = time.monotonic() + getattr(args, "startup_timeout", 30.0)
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('监控进程未启动，请查看本地日志：' + str(log))
            current = store.read()
            if (current.get('startupToken') == startup_token and
                    current.get('enabled') is True and running(args.state_dir, args.thread_id, args.host)):
                break
            time.sleep(0.05)
        else:
            store.cancel()
            process.terminate()
            raise RuntimeError('后台启动未确认，已取消本次启动，请查看日志：' + str(log))
    finally:
        config_path.unlink(missing_ok=True)
    emit({'pid': current['pid'], 'threadId': args.thread_id, 'hostId': args.host, 'log': str(log),
          'stop': './guard --host ' + args.host + ' stop ' + args.thread_id})


def host_snapshot(args, descriptor, deadline=None):
    """Independent read-only host query; one disconnected SSH host cannot hide local chats."""
    host_id = descriptor['id']
    result = {'id': host_id, 'name': descriptor['name'], 'canControl': host_id == 'local',
              'connected': False, 'quotaKnown': False, 'windows': [], 'threads': []}
    try:
        reader = ReadOnlyServer(binary_for(args.app), args.home) if host_id == 'local' else RemoteReadOnlyServer(descriptor)
        budget = min(12, deadline - time.monotonic()) if deadline is not None else 12
        with reader.bounded(budget), reader as rpc:
            if host_id != 'local':
                version = daemon_version(rpc.initialization)
                result.update(remoteVersion=version, canControl=True,
                              controlConnected=True)
            # Listing remains available when the account's quota cannot be read.
            listed = {'data': query_threads(rpc)}
            result['connected'] = True
            for t in listed.get('data', []):
                try:
                    thread_id = uuid_arg(t['id'])
                except (ValueError, KeyError, TypeError, argparse.ArgumentTypeError):
                    continue
                result['threads'].append({'id': thread_id, 'hostId': host_id,
                    'hostName': descriptor['name'], 'canControl': result['canControl'],
                    'name': t.get('name') or t.get('preview', '')[:60] or '未命名聊天',
                    'cwd': t.get('cwd'), 'runtimeStatus': t.get('status')})
            try:
                raw = rpc.query('account/rateLimits/read')
                config = getattr(args, 'configuration', settings.DEFAULTS)
                now = time.time()
                q = quota(raw, now, config['threshold'], config['resumeRemaining'])
                result.update(quotaKnown=q.known, windows=q.windows, quotaReason=q.reason,
                              forecast=forecast(raw, now, config['threshold'], config['resumeRemaining']))
            except Exception:
                result['quotaReason'] = '该主机额度暂不可用，请检查登录和网络'
    except Exception:
        result['reason'] = ('本机查询连接不可用' if host_id == 'local' else
                            'SSH 只读连接不可用；请在 Codex 中连接该主机，确认系统 SSH 可免交互登录')
    return result


def gui_snapshot(args):
    """Single read-only backend call for a native UI; no monitoring enrollment."""
    check_version(args.app)
    warnings = []
    try:
        args.configuration = settings.load(args.state_dir)
    except (OSError, ValueError, TypeError):
        args.configuration = dict(settings.DEFAULTS)
        warnings.append('设置文件不可读，当前显示默认值；请保存设置后启动监控')
    inventory = [{'id': 'local', 'name': '本机'}]
    try:
        inventory += hosts(args.home)
    except (OSError, ValueError, TypeError):
        warnings.append('SSH 主机配置暂不可读')
    deadline = time.monotonic() + 15
    with ThreadPoolExecutor(max_workers=min(4, len(inventory))) as pool:
        snapshots = list(pool.map(lambda h: host_snapshot(args, h, deadline), inventory))
    try:
        ipc = Desktop(args.home, str(args.app), timeout=3)
        with ipc.bounded(max(0, deadline - time.monotonic())), ipc:
            snapshots[0]['controlConnected'] = True
    except Exception:
        snapshots[0]['controlConnected'] = False
    threads = [t for h in snapshots for t in h.pop('threads')]
    emit({'connected': any(h['connected'] for h in snapshots), 'hosts': snapshots,
          'threads': threads, 'monitors': statuses(args.state_dir), 'warnings': warnings,
          'settings': args.configuration})


def interactive(args):
    doctor(args)
    threads = list_threads(args)
    print('\n请选择需要监控的原聊天（输入 0 退出）：')
    for i, t in enumerate(threads, 1):
        print(f'{i:2}. {t["name"]}\n    {t["id"]}')
    try:
        choice = int(input('聊天编号：').strip())
    except ValueError:
        raise RuntimeError('请输入列表中的聊天编号，输入 0 退出') from None
    if choice == 0:
        return
    if not 1 <= choice <= len(threads):
        raise RuntimeError('编号超出范围')
    args.thread_id = threads[choice - 1]['id']
    result = check(args, args.thread_id)
    emit(result)
    store = Store(args.state_dir, args.thread_id, args.host)
    if not can_enroll(result, store.data):
        return
    print('\n1 = 只读预览；2 = 启用后台监控（小时额度 ≤5% 中断，小时额度恢复后续跑）；0 = 退出')
    mode = input('模式：').strip()
    if mode == '1':
        args.execute = False
        preview(args)
    elif mode == '2':
        background(args)


def parser():
    p = argparse.ArgumentParser(description='个人 Codex 桌面用量监控与原聊天续跑')
    p.add_argument('--app', type=Path, default=default_app())
    p.add_argument('--home', type=Path, default=__import__('codex_relay.store', fromlist=['default_paths']).default_paths().codex_home)
    p.add_argument('--state-dir', type=Path, default=default_state_dir())
    p.add_argument('--host', default='local', help='原聊天所属的桌面 hostId；本机或已配置的 SSH 主机')
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor', help='只读检查桌面连接和额度')
    ls = sub.add_parser('list', help='列出原聊天，不启用监控')
    ls.add_argument('--limit', type=int, default=30)
    for name in ('check', 'stop'):
        sub.add_parser(name).add_argument('thread_id', type=uuid_arg)
    sub.add_parser('status')
    sub.add_parser('gui-snapshot', help=argparse.SUPPRESS)
    cfg = sub.add_parser('settings', help='读取或保存监控设置')
    cfg.add_argument('--file', type=Path)
    for name in ('watch', 'background', 'interactive'):
        w = sub.add_parser(name)
        if name != 'interactive':
            w.add_argument('thread_id', type=uuid_arg)
        w.add_argument('--threshold', type=float, default=None)
        w.add_argument('--resume-remaining', type=float, default=None)
        w.add_argument('--interval', type=float, default=None)
        w.add_argument('--config-file', type=Path, default=None, help='从 JSON 加载本次监控配置')
        w.add_argument('--message-file', type=Path, default=None, help='UTF-8 续跑文字文件')
        w.add_argument('--limit-id', default='codex')
        w.add_argument('--max-resumes', type=int, default=0, help='0 = 不设次数上限，正常结束时停止')
        w.add_argument('--requested-at-ns', type=int, default=REQUEST_TIME_NS, help=argparse.SUPPRESS)
        w.add_argument('--startup-token', default=None, help=argparse.SUPPRESS)
        w.add_argument('--connection-key', default=None, help=argparse.SUPPRESS)
        if name == 'background':
            w.add_argument('--startup-timeout', type=float, default=30.0)
        if name == 'watch':
            w.add_argument('--execute', action='store_true', help='实际中断并续跑；默认只读预览')
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    args.app, args.home, args.state_dir = (x.expanduser().resolve() for x in (args.app, args.home, args.state_dir))
    if args.command in ('watch', 'background', 'interactive'):
        config = settings.validate(json.loads(args.config_file.read_text())) if args.config_file else settings.load(args.state_dir)
        for arg, key in (('threshold', 'threshold'), ('resume_remaining', 'resumeRemaining'), ('interval', 'interval')):
            if getattr(args, arg) is not None: config[key] = getattr(args, arg)
        if args.message_file: config['continuation'] = args.message_file.read_text()
        if args.command == 'background' and (not math.isfinite(args.startup_timeout) or args.startup_timeout <= 0):
            p.error('startup-timeout must be finite and positive')
        config = settings.validate(config)
        args.threshold, args.resume_remaining, args.interval, args.continuation = (config[k] for k in ('threshold', 'resumeRemaining', 'interval', 'continuation'))
        if (not 0 <= args.threshold < args.resume_remaining <= 100 or not math.isfinite(args.interval) or args.interval <= 0 or
                args.max_resumes < 0):
            p.error('参数要求：0 ≤ 暂停阈值 < 续跑阈值 ≤ 100；检查间隔必须为有限正数；次数上限 ≥ 0')
    if args.command == 'settings':
        if args.file:
            emit(settings.write(args.state_dir / 'settings.json', json.loads(args.file.read_text())))
        else:
            emit(settings.load(args.state_dir))
    elif args.command == 'doctor':
        doctor(args)
    elif args.command == 'list':
        emit(list_threads(args))
    elif args.command == 'check':
        emit(check(args, args.thread_id))
    elif args.command == 'watch':
        watch(args)
    elif args.command == 'background':
        background(args)
    elif args.command == 'interactive':
        interactive(args)
    elif args.command == 'gui-snapshot':
        gui_snapshot(args)
    elif args.command == 'stop':
        Store(args.state_dir, args.thread_id, args.host).cancel()
        emit({'ok': True, 'threadId': args.thread_id, 'hostId': args.host})
    elif args.command == 'status':
        emit(statuses(args.state_dir))


def entrypoint():
    try:
        main()
    except KeyboardInterrupt:
        print('\n已退出。')
    except Exception as exc:
        # Do not print raw conversation data, RPC replies, auth material or tracebacks.
        message = ('查询超时，请检查连接后刷新；本次检查已结束' if isinstance(exc, TimeoutError) else
                   str(exc) if isinstance(exc, (RuntimeError, ValueError)) else '操作失败，请检查 App、网络或本地文件')
        print('错误：' + message, file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    entrypoint()
