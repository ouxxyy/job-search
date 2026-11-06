"""Per-platform completion contract shared by CLI, reports and the skill agent."""
from .core import PLATFORMS, safe_url, target_count
from collections import Counter
from .sources import query_routes


def browser_budget(plan, state):
    """Expose recorded usage, never turn missing counters into exhausted budgets."""
    pages = state.get('pages_read')
    queries = state.get('queries')
    if pages is None and isinstance(queries, list):
        counts = [q.get('pages') if isinstance(q, dict) else None for q in queries]
        if all(type(n) is int and n >= 0 for n in counts):
            pages = sum(counts)
    attempted = state.get('details_attempted')
    read = state.get('details_read')
    result = {}
    for name, used, limit in [('pages', pages, plan['max_pages_per_platform']),
                              ('details', attempted, plan['max_details_per_platform'])]:
        known = type(used) is int and used >= 0
        result[name] = {'used': used if known else None, 'limit': limit,
                        'remaining': max(0, limit - used) if known else None}
    result['details_read'] = read if type(read) is int and read >= 0 else None
    return result


def collection_step(plan, state, mine):
    """Choose the next useful bounded operation from existing evidence."""
    budget = browser_budget(plan, state)
    visited = {safe_url(u) for u in state.get('detail_urls', []) + state.get('original_detail_urls', [])}
    # Strict unknown-policy exclusions can still be verified; known hard
    # failures cannot. Deduplicate links even when a source supplies two IDs.
    unresolved = [j for j in mine if j.get('unknowns') and not j.get('rejections')]
    candidates = []
    for j in unresolved:
        links = [('platform', safe_url(j.get('link'), j['platform']))]
        if j.get('source_kind') == 'announcement':
            links.insert(0, ('original', safe_url(j.get('original_url'))))
        for kind, url in links:
            if url and url not in visited:
                candidates.append({**j, '_detail_url':url, '_detail_kind':kind})
                break
    candidates.sort(key=lambda j: (-j.get('score', 0), len(j.get('unknowns', [])), j.get('key', '')))
    unique = {}
    for j in candidates:
        unique.setdefault(j['_detail_url'], j)
    candidates = list(unique.values())
    pages, details = budget['pages'], budget['details']
    stop = state.get('stop_reason')
    # A result-page cap does not prevent reading existing candidates, and a
    # detail cap does not prohibit remaining result pages. No flag alone ends work.
    if stop in ('budget_exhausted', 'results_exhausted') and (
            pages['used'] is None or details['used'] is None
            or (stop == 'results_exhausted' and pages['used'] == 0)):
        action = 'audit_budget'
        message = '缺少有效的实际页数或详情尝试计数，先从已有记录补齐；不能仅凭停止标记结束，也不要从头重扫。'
    elif candidates and details['remaining'] != 0:
        action = 'verify_details'
        message = '优先核实已有高匹配候选，每批最多 5 个；记录尝试、成功读取及已访问链接，再检查进度。'
    elif stop != 'results_exhausted' and pages['remaining'] != 0:
        action = 'collect_more'
        message = '继续已确认查询的下一页，每批 1 页后检查进度；从现有页面续接，不重复扫描首页。'
    elif stop == 'results_exhausted' or pages['remaining'] == 0:
        action = 'shortfall'
        message = ('当前查询已无更多结果' if stop == 'results_exhausted' else '已达到结果页上限')
        message += ('，已达到详情尝试上限' if details['remaining'] == 0 else '，没有尚未尝试且有详情链接的可核实候选')
        message += '；如实交付不足，不降低条件凑数。'
    else:
        action = 'audit_budget'
        message = '核对实际预算使用情况与可继续的查询。'
    usage = (f'结果页 {pages["used"] if pages["used"] is not None else "未记录"}/{pages["limit"]}；'
             f'详情尝试 {details["used"] if details["used"] is not None else "未记录"}/{details["limit"]}；'
             f'成功读取 {budget["details_read"] if budget["details_read"] is not None else "未记录"}。')
    gaps = Counter(u for j in unresolved for u in j.get('unknowns', []))
    if gaps:
        usage += '主要缺证：' + '；'.join(f'{u}（{n} 条）' for u, n in gaps.most_common(3)) + '。'
    queue = [{'key': j.get('key'), 'title': j['title'], 'url': j['_detail_url'], 'source':j['_detail_kind'], 'unknowns': j.get('unknowns', [])}
             for j in candidates[:min(5, details['remaining']) if details['remaining'] is not None else 5]]
    return action, message + usage, budget, queue


def login_prompt(platform, state):
    url = safe_url(state.get('loginLink'), platform) or safe_url(state.get('url'), platform) or PLATFORMS[platform]['home']
    return (f'请先在 Tabbit 登录【{PLATFORMS[platform]["name"]}】：{url}。'
            '验证码请手动完成；登录后告诉我“已登录”，我会继续这个平台。'
            '如果暂时不搜，也请明确告诉我跳过；不会把未登录记为零岗位或默认跳过。')


def platform_progress(plan, jobs, statuses):
    rows = []
    for key in plan['platforms']:
        state = statuses.get(key, {'status': 'not_scanned'})
        mine = [j for j in jobs if j['platform'] == key]
        qualified = sum(j['bucket'] == 'shortlist' for j in mine)
        review = sum(j['bucket'] == 'review' for j in mine)
        target = target_count(plan, key)
        missing = max(0, target - qualified)
        status = state.get('status', 'not_scanned')
        url = safe_url(state.get('loginLink'), key) or safe_url(state.get('url'), key) or PLATFORMS[key]['home']
        routes = query_routes(plan, key)
        if key == 'bendibao' and state.get('url', '') in ('', PLATFORMS[key]['home']):
            url = routes[0]['entry_url']
        budget, detail_queue = browser_budget(plan, state), []
        if status == 'skipped' and state.get('note'):
            action, message = 'shortfall', '用户明确跳过此平台：' + state['note']
        elif status == 'login_required':
            action, message = 'ask_login', login_prompt(key, state)
        elif status in ('model_rate_limited','host_rate_limited','platform_rate_limited'):
            action = 'pause_platform' if status=='platform_rate_limited' else 'pause_model'
            message = ('招聘平台明确返回 HTTP 429，停止该平台请求，按 Retry-After 或后续明确恢复指令继续，不要求重新登录。'
                       if status=='platform_rate_limited' else
                       'Codex/宿主请求链路限流，暂停本轮自动调用并保留进度；不要切换平台、创建子 Agent 或循环重试，也不要让用户重新登录招聘平台。')
        elif status == 'anti_bot_cooldown':
            action = 'pause_platform'
            message = ('平台把自动化页面立即置为 about:blank（反自动化反制）。停止读取、刷新和反复检查已有标签；'
                       '已捕获证据保留在报告中。这不是未登录，不需要用户操作。'
                       '建议等待 20-30 分钟冷却后用一次性新页面低速恢复（scan --only 本平台），不从头重扫。')
        elif status in ('browser_error','wrong_context','browser_busy','access_denied'):
            action = 'inspect_existing_tab'
            message = {'browser_error':'Agent 先读取已有同平台标签，核实页面实际状态；空白页不证明未登录，不把浏览器排查交给用户。',
                       'wrong_context':'Agent 先从可见导航寻找求职入口；当前是招聘方页面，不是未登录，不填写企业认证表单。',
                       'browser_busy':'已有标签被其他任务占用，等待释放；不得新建标签或任务绕过占用。',
                       'access_denied':'Agent 只读核实已有页面的访问提示；只有真实验证码或登录墙才请用户操作。'}[status]
        elif status in ('blocked', 'app_required'):
            action = 'resolve_access'
            message = {'blocked': '平台要求验证或限制访问，请用户手动处理后恢复；本平台仍未完成。',
                       'app_required': '网页无法完成求职搜索，说明需要 App，并请用户提供岗位链接或明确跳过。'}[status]
        elif qualified >= target and status in ('ok', 'empty'):
            action, message = 'done', f'已找到 {qualified}/{target} 个符合条件的岗位。'
        elif status in ('not_scanned', 'guided_pending', 'needs_inspection', 'imported', 'ready'):
            action = 'tabbit_search'
            message = f'必须继续用 Tabbit 在{PLATFORMS[key]["name"]}执行已确认查询；目标 {target} 个。不能在只完成 BOSS/猎聘后结束。'
        elif missing:
            action, message, budget, detail_queue = collection_step(plan, state, mine)
            message = f'符合 {qualified}/{target} 个，待核实 {review} 个，还差 {missing} 个。' + message
        else:
            action, message = 'verify_coverage', '数量已达到，但查询覆盖状态未确认；核对并记录实际完成的查询。'
        rows.append({'platform':key, 'name':PLATFORMS[key]['name'], 'target':target,
                     'qualified':qualified, 'review':review, 'missing':missing, 'status':status,
                     'action':action, 'message':message, 'url':url,
                     'budget':budget, 'detail_queue':detail_queue, 'search_routes':routes})
    return rows


def next_actions(plan, jobs, statuses):
    rows = platform_progress(plan, jobs, statuses)
    pending = [r for r in rows if r['action'] not in ('done', 'shortfall')]
    model_paused = any(r['action']=='pause_model' for r in rows)
    return {'complete':all(r['action'] == 'done' for r in rows),
            'execution_paused':model_paused,
            'can_deliver_final':not pending,
            'target_total':sum(r['target'] for r in rows),
            'qualified_total':sum(r['qualified'] for r in rows),
            'needs_user':[r['platform'] for r in pending if r['action'] in ('ask_login','resolve_access')],
            'needs_agent':[] if model_paused else [r['platform'] for r in pending if r['action'] not in ('ask_login','resolve_access','pause_model','pause_platform')],
            'deferred_until_resume':[r['platform'] for r in pending if r['action']!='pause_model'] if model_paused else [],
            'paused':[r['platform'] for r in pending if r['action'] in ('pause_model','pause_platform')],
            'platforms':rows}


def write_actions(out, plan, jobs, statuses):
    import json
    from pathlib import Path
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    actions = next_actions(plan, jobs, statuses)
    (out/'next-actions.json').write_text(json.dumps(actions, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# 本轮还需要完成什么', '',
             f'目标 {actions["target_total"]} 个；已符合 {actions["qualified_total"]} 个。', '',
             '以下登录提示必须直接发给用户；界面搜索事项由 Agent 继续，不得静默省略平台。', '']
    for row in actions['platforms']:
        lines.extend([f'## {row["name"]} · {row["qualified"]}/{row["target"]}', '', row['message'], ''])
    (out/'next-actions.md').write_text('\n'.join(lines), encoding='utf-8')
    return actions
