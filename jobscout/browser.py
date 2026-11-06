"""Bounded Tabbit transport. No direct HTTP, credentials export or request replay."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid
from contextlib import contextmanager
from urllib.parse import urlsplit
from urllib.parse import urlencode
from .core import PLATFORMS
ROOT = Path(__file__).resolve().parent.parent

class TabbitError(RuntimeError):
    def __init__(self, message, code='browser_error'):
        super().__init__(message)
        self.code = code


def raise_transport_error(error):
    message = str(error.get('message', error) if isinstance(error, dict) else error)
    code = error.get('code', '') if isinstance(error, dict) else ''
    if code == 'MODEL_RATE_LIMITED':
        raise TabbitError('模型服务限流；保留进度，停止自动调用，不要求招聘平台重新登录。', 'model_rate_limited')
    if '429' in message or 'RATE_LIMIT' in code:
        raise TabbitError('宿主请求链路限流；未发现招聘平台 HTTP 响应证据，不归因为网站限流。', 'host_rate_limited')
    raise TabbitError('Tabbit 程序失败：'+message[:240])

def launcher():
    explicit = os.environ.get('TABBIT_CLI')
    if explicit:
        p = Path(explicit).expanduser()
    elif os.name == 'nt':
        p = Path(os.environ.get('LOCALAPPDATA', '')) / 'Tabbit/LocalAgent/bin/tabbit-cli.exe'
    else:
        p = Path.home() / '.local/bin/tabbit-cli'
    if not p.is_file():
        found = shutil.which('tabbit-cli')
        if not found:
            raise TabbitError('未找到 Tabbit 稳定启动器。安装 Tabbit 或设置 TABBIT_CLI；详见 README。')
        p = Path(found)
    return str(p)

def command(args, stdin=None, timeout=90):
    try:
        r = subprocess.run([launcher(), *args], input=stdin, capture_output=True, text=True, encoding='utf-8', timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise TabbitError(f'Tabbit 调用失败：{type(e).__name__}；使用 diagnose 检查，勿盲目重试') from e
    try:
        data = json.loads(r.stdout[r.stdout.index('{'):])
    except (ValueError, json.JSONDecodeError) as e:
        raise TabbitError(f'Tabbit 无有效 JSON（退出码 {r.returncode}）；使用 diagnose 检查') from e
    if data.get('ok') is False:
        error = data.get('error', {})
        raise_transport_error(error)
    return data

def node(task, js, *, tab_id=None):
    req = 'scout-' + uuid.uuid4().hex[:12]
    args = ['nodejs', '--task', task, '--request-id', req, '--timeout-ms', '60000']
    if tab_id is not None:
        args += ['--tab', str(tab_id)]
    data = command(args, js)
    deadline = time.monotonic() + 90
    while data.get('status') in ('queued', 'running') and time.monotonic() < deadline:
        data = command(['receipt', '--task', task, '--request-id', req, '--wait-ms', '20000'], timeout=35)
    if data.get('status') != 'succeeded':
        error = data.get('error') or data.get('result', {}).get('error')
        if error:
            raise_transport_error(error)
        raise TabbitError(f'Tabbit 程序 {data.get("status", "unknown")}；检查 receipt {req}，不要更换 ID 重放')
    result = data.get('result', {})
    if 'value' in result:
        return result['value']
    rid = result.get('resourceId')
    if not rid:
        raise TabbitError('成功回执缺少结果')
    parts, offset = [], 0
    for _ in range(100):
        chunk = command(['resource', '--task', task, '--resource', rid, '--offset', str(offset), '--max-bytes', '32768'])
        parts.append(chunk.get('data', ''))
        if chunk.get('eof'):
            return json.loads(''.join(parts))
        nxt = chunk.get('nextOffset')
        if not isinstance(nxt, int) or nxt <= offset:
            break
        offset = nxt
    raise TabbitError('resource 未读取完整；拒绝把截断数据当结果')

def finish(task):
    data = command(['finish', '--task', task], timeout=40)
    if data.get('forcedCleanup') or data.get('ok') is False:
        raise TabbitError('Tabbit 清理未正常完成；保留诊断，不宣称已释放任务')
    return data

# Keep rendered-state detection testable separately from transport.
STATE_JS = (ROOT / 'assets/page-state.js').read_text(encoding='utf-8')


@contextmanager
def run_lock():
    """One browser run per OS account; an OS lock releases automatically on crash."""
    folder = Path(os.environ.get('JOB_SCOUT_STATE_DIR', str(Path.home()/'.local/state/job-scout'))).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    with (folder/'browser.lock').open('a+b') as handle:
        if os.name == 'nt':
            import msvcrt
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b'0'); handle.flush()
            handle.seek(0)
            try: msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as e: raise TabbitError('BROWSER_RUN_BUSY：已有 Job Search 正在运行；不要并发启动或重复重试。') from e
        else:
            import fcntl
            try: fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as e: raise TabbitError('BROWSER_RUN_BUSY：已有 Job Search 正在运行；不要并发启动或重复重试。') from e
        try: yield
        finally:
            if os.name == 'nt':
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def inventory(task):
    data = command(['create', '--task', task])
    tabs, seen = list(data.get('tabs', [])), set()
    for _ in range(20):
        if not data.get('truncated'):
            return tabs
        cursor = data.get('nextCursor')
        if not cursor or cursor in seen:
            raise TabbitError('INVENTORY_INCOMPLETE：标签清单不完整，不能假定没有已登录标签。')
        seen.add(cursor)
        data = command(['tabs', '--task', task, '--cursor', cursor])
        tabs.extend(data.get('tabs', []))
    raise TabbitError('INVENTORY_INCOMPLETE：停止分页，保留已有标签。')


def platform_tabs(tabs, platform, entry_url=None):
    domain = PLATFORMS[platform]['host']
    def matches(tab):
        try:
            host = urlsplit(tab.get('url', '')).hostname or ''
            return host == domain or host.endswith('.'+domain)
        except ValueError: return False
    def priority(tab):
        u = urlsplit(tab['url'])
        employer = u.hostname in ('lpt.liepin.com', 'u.bendibao.com') or '/web/boss/' in u.path or u.path.startswith('/ent/') or u.path.rstrip('/') == '/job/recruit'
        jobs = any(x in u.path for x in ('/web/geek/jobs', '/web/geek/job', '/zhaopin/', '/jobs/', '/pc/search'))
        target_host = urlsplit(entry_url or '').hostname if platform == 'bendibao' else None
        station_match = target_host and u.hostname in (target_host, 'm.' + target_host)
        return (employer, not station_match, not jobs, not tab.get('active', False), str(tab['tabId']))
    return sorted((t for t in tabs if matches(t)), key=priority)


def inspect(task, platform, *, existing_only=False, entry_url=None):
    from .core import safe_url
    entry_url = entry_url or PLATFORMS[platform]['home']
    if not safe_url(entry_url, platform):
        raise ValueError('预检入口必须属于对应平台')
    candidates = platform_tabs(inventory(task), platform, entry_url)
    available = [t for t in candidates if t.get('state') in ('available', 'owned')]
    if candidates and not available:
        return {'platform':platform,'status':'browser_busy','login':'unknown',
                'url':candidates[0]['url'],'reused_tab':False,
                'note':'已有同平台标签正被其他任务占用；等待该任务释放，不新开页面绕过。'}
    # Prefer already-open job pages. Never navigate or refresh existing user tabs at preflight.
    # The evaluate is raced: on a page the platform navigates to about:blank it can
    # hang until the 60s program deadline, which would kill the whole CLI step.
    read = ('const readState = '+STATE_JS+';\n'
            'return await Promise.race([page.evaluate(readState).catch(()=>null),'
            'new Promise(r=>setTimeout(()=>r(null),8000))]);')
    checked = []
    for tab in available[:3]:
        try:
            state = node(task, read, tab_id=tab['tabId'])
        except TabbitError as e:
            if e.code!='browser_error' or not any(x in str(e).lower() for x in ('no longer attached','target closed','page closed')):
                raise
            state = {'status':'browser_error','login':'unknown','url':tab['url'],
                     'note':'已有标签在获取后脱离浏览器连接；保持未知，不刷新、不重新开页、不要求用户重新登录。'}
        if not isinstance(state, dict):
            state = {'status':'browser_error','login':'unknown','url':tab['url'],
                     'note':'页面状态读取超时（页面可能正被平台置空导航）；保持未知，不刷新重试。'}
        state.update(platform=platform, tab_id=tab['tabId'], reused_tab=True)
        checked.append(state)
        if state['status'] == 'ready': return state
    if checked:
        if len(available)>3 or any(t.get('state')=='claimed' for t in candidates):
            return {**checked[0], 'status':'needs_inspection','login':'unknown',
                    'note':'尚有未读取或被占用的同平台标签；不能断言需要重新登录。'}
        # A real job page has priority over a recruiting-side tab. UI errors are agent work.
        # Unresolved existing pages remain agent work, rather than asking the user to relogin.
        priority = ('wrong_context','needs_inspection','browser_error','blocked','login_required')
        return min(checked, key=lambda s: priority.index(s['status']) if s['status'] in priority else -1)
    if existing_only:
        return {'platform':platform,'status':'needs_inspection','login':'unknown',
                'url':entry_url,'reused_tab':False,
                'note':'当前没有可读的同平台现有标签；只读核验不新开页面。'}
    # Only when a complete inventory proves no same-platform tab exists, open one scratch page.
    js = 'const readState = '+STATE_JS+';\nconst url = '+json.dumps(entry_url)+';\n'+r"""
const p = await context.newPage();
try {
 await p.goto(url,{waitUntil:'domcontentloaded',timeout:25000});
 // Short settle only: platforms blank automation-created pages seconds after
 // render, so a long wait here reads the blank instead of the real state. The
 // evaluate is raced because it can hang on a page being navigated to about:blank.
 await p.waitForFunction(() => (document.body?.innerText||'').trim().length>100,null,{timeout:2000}).catch(()=>{});
 const st = await Promise.race([p.evaluate(readState).catch(()=>null),new Promise(r=>setTimeout(()=>r(null),8000))]);
 // This scratch page is ours: close it immediately unless the user must act on
 // it (login wall / verification), so its later blanking is never visible.
 if(!st || (st.status!=='login_required' && st.status!=='blocked')) await p.close().catch(()=>{});
 return st;
} catch(e) { return {status:'browser_error',login:'unknown',url:p.url()}; }
"""
    state = node(task, js) or {'status':'browser_error','login':'unknown'}
    state.update(platform=platform,reused_tab=False)
    return state

def search_url(platform, keyword, city):
    if platform == 'boss':
        cities = json.loads((ROOT / 'data/city_codes_boss.json').read_text())
        code = '' if city == '全国' else cities.get(city)
        if code is None:
            raise ValueError('BOSS 城市未收录：' + city + '；使用 Tabbit 界面选择，不猜城市码')
        return 'https://www.zhipin.com/web/geek/job?' + urlencode({'query': keyword, 'city': code})
    if platform == 'liepin':
        cities = json.loads((ROOT / 'data/city_codes_liepin.json').read_text())
        code = '' if city == '全国' else cities.get(city)
        if code is None:
            raise ValueError('猎聘城市未收录：' + city + '；使用 Tabbit 界面选择，不猜城市码')
        return 'https://www.liepin.com/zhaopin/?' + urlencode({'key': keyword, 'dqs': code})
    raise ValueError('此平台由 Agent 按当前页面控件搜索，见 references/platforms.md')

def scan(task, platform, keyword, city, page_budget=1, *, tab_id=None):
    # Prefer the already-open warm tab: platforms blank automation-created pages
    # seconds after render, while user-opened tabs keep working. A fresh
    # disposable page is only the no-existing-tab fallback, and its capture
    # returns before the blank window (response ~1.5s, blank ~2-6s).
    config = json.dumps({'platform': platform, 'url': search_url(platform, keyword, city), 'budget': page_budget, 'reuse': tab_id is not None}, ensure_ascii=False)
    source = (ROOT / 'assets/collect.js').read_text(encoding='utf-8')
    return node(task, 'const CFG = ' + config + ';\nconst readState = ' + STATE_JS + ';\n' + source, tab_id=tab_id)
