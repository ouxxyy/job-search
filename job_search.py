#!/usr/bin/env python3
"""Job Search CLI — prepare → human confirmation → Tabbit → evidence → shortlist."""
from __future__ import annotations
import argparse
import datetime as dt
import json
from pathlib import Path
import random
import sys
import time
from jobscout.core import DEFAULTS, PLATFORMS, build_report, digest, safe_url, validate_plan, target_count
from jobscout.report import write_report
from jobscout.workflow import login_prompt, write_actions
from jobscout.sources import query_routes, search_routes

ROOT=Path(__file__).resolve().parent

def read_json(path):
    return json.loads(Path(path).expanduser().read_text(encoding='utf-8'))

def save(path,data):
    path=Path(path).expanduser();path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')

def plan_at(path):
    return validate_plan(read_json(path))


def report_receipt(meta, out, verbose=False):
    """Keep normal model context bounded; full details already live in summary.json."""
    if verbose:
        return meta
    return {'status':meta['status'],'out':str(Path(out).expanduser().resolve()),
            'counts':meta['counts'],'target_total':meta['targets']['target_total'],
            'can_deliver_final':meta['targets']['can_deliver_final'],
            'execution_paused':meta['targets']['execution_paused'],
            'platforms':[{k:r[k] for k in ('platform','target','qualified','review','action')} for r in meta['targets']['platforms']],
            'details':'summary.json / next-actions.json（按需读取，不反复回传全部原文）'}

def validate_statuses(raw, p):
    if not isinstance(raw,dict) or any(k not in p['platforms'] for k in raw):
        raise ValueError('平台状态必须是计划中平台的映射')
    allowed={'ok','empty','login_required','blocked','browser_error','needs_inspection','guided_pending','app_required','not_scanned','partial','imported','ready','skipped','wrong_context','browser_busy','access_denied','model_rate_limited','host_rate_limited','platform_rate_limited','anti_bot_cooldown'}
    for k,s in raw.items():
        if not isinstance(s,dict) or s.get('status') not in allowed:
            raise ValueError('未知平台状态：'+k)
        if s.get('url') and not safe_url(s['url'],k):
            raise ValueError('平台状态 URL 不合法')
        if s['status']=='skipped' and (not isinstance(s.get('note'),str) or not s['note'].strip()):
            raise ValueError('跳过平台必须在 note 中记录用户的明确指令')
        for field in ('pages_read','details_attempted','details_read'):
            if field in s and (type(s[field]) is not int or s[field] < 0):
                raise ValueError(field+' 必须为非负整数')
        if 'details_read' in s and s['details_read'] > s.get('details_attempted', 0):
            raise ValueError('details_read 不能超过 details_attempted')
        visited=set()
        for field in ('detail_urls','original_detail_urls'):
            if field not in s:continue
            urls=s[field]
            if not isinstance(urls,list) or any(not isinstance(u,str) or not safe_url(u,k if field=='detail_urls' else None) for u in urls):
                raise ValueError(field+' 必须是已尝试的 HTTP(S) 详情链接数组；detail_urls 限本平台')
            visited.update(safe_url(u) for u in urls)
        if len(visited) > s.get('details_attempted',0):
            raise ValueError('详情链接去重总数不能超过 details_attempted')
        if 'queries' in s:
            queries=s['queries']
            if not isinstance(queries,list) or any(not isinstance(q,dict) or type(q.get('pages')) is not int or q['pages'] < 0 for q in queries):
                raise ValueError('queries 必须记录各查询的非负整数 pages')
            if 'pages_read' in s and s['pages_read'] != sum(q['pages'] for q in queries):
                raise ValueError('pages_read 与 queries 页数之和不一致')
    return raw

def demo(out):
    p=validate_plan({'keywords':['财务分析师'],'cities':['上海'],'salary_min_k':15,'salary_max_k':30,
                     'plus_keywords':['预算','经营分析'],'platforms':['boss','liepin','maimai','zhaopin','51job']})
    now=dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    specs=[('boss','财务分析师','示例企业 · 澄川制造','18-25K·14薪','3天前','今天活跃'),
           ('liepin','高级财务分析师','示例企业 · 岸行供应链','22-30K','12天前','2天前活跃'),
           ('zhaopin','财务分析师（经营方向）','示例企业 · 望野消费品','16-22K','6天前','在线'),
           ('51job','财务分析师','示例企业 · 新序医疗器械','15-20K','',''),
           ('liepin','财务分析师','示例企业 · 远帆物流','12-18K','8天前',''),
           ('boss','财务分析师','示例企业 · 拾方科技','20-28K','95天前','刚刚在线'),
           ('boss','会计助理','示例企业 · 林间商贸','8-10K','1天前','在线')]
    jobs=[]
    for i,(plat,title,company,salary,date,active) in enumerate(specs):
        jobs.append({'id':'demo-'+str(i),'platform':plat,'title':title,'company':company,'salary':salary,
          'city':'上海','experience':'3-5年','degree':'本科','industry':'制造业' if i==0 else '企业服务','scale':'100-499人',
          'description':'负责预算与经营分析，协同业务部门复盘经营结果。','skills':['预算','经营分析'],
          'publish_time':date,'refresh_time':'今天' if not date else '', 'hr_active':active,
          'link':PLATFORMS[plat]['home'],'source_url':PLATFORMS[plat]['home'],
          'captured_at':now.isoformat(timespec='seconds'),'evidence':'虚构示例：'+title+'；'+salary+'；上海；'+(date+'发布' if date else '仅显示今天刷新')})
    statuses={k:{'status':'ok','jobs':sum(j['platform']==k for j in jobs),'url':PLATFORMS[k]['home'],'note':'界面演示用虚构数据，不是实际扫描结果'} for k in p['platforms']}
    statuses['maimai']={'status':'login_required','jobs':0,'url':PLATFORMS['maimai']['home'],'note':'演示登录未完成状态：提示登录、保留入口，其他平台继续'}
    return write_report(build_report(jobs,p),p,statuses,out,demo=True)

def main():
    ap=argparse.ArgumentParser(prog='job-search',description='生成有依据的中文求职短名单；默认不搜索，必须先确认计划。')
    sub=ap.add_subparsers(dest='cmd',required=True)
    sub.add_parser('platforms',help='查看平台能力与入口')
    sub.add_parser('doctor',help='只检查本机依赖，不打开平台')
    routes=sub.add_parser('routes',help='生成按已确认城市和岗位划分的渠道入口，不访问网络')
    routes.add_argument('--plan',required=True);routes.add_argument('--out',required=True)
    prepare=sub.add_parser('plan',help='规范化条件，输出待确认计划摘要；不访问浏览器')
    prepare.add_argument('--input');prepare.add_argument('--keyword',action='append');prepare.add_argument('--city',action='append')
    prepare.add_argument('--platforms');prepare.add_argument('--out',required=True)
    prepare.add_argument('--count',type=int,help='每个平台的目标岗位数，默认 5；可与 --input 一起修改计划')
    prepare.add_argument('--platform-count',action='append',default=[],metavar='平台=数量',help='单个平台覆盖数量，例如 boss=10；可重复')
    prepare.add_argument('--pages',type=int,help='每平台结果页总上限（1–60），优先于自动预算')
    prepare.add_argument('--details',type=int,help='每平台详情尝试总上限（1–600），优先于自动预算')
    prepare.add_argument('--auto-budget',action='store_true',help='显式重新计算输入计划的预算；生成新计划后须确认')
    pre=sub.add_parser('preflight',help='仅检查平台登录与访问状态，不进行岗位搜索')
    pre.add_argument('--plan',required=True);pre.add_argument('--out',required=True);pre.add_argument('--only',help='仅重检这些平台，逗号分隔')
    scan=sub.add_parser('scan',help='确认后采集 BOSS/猎聘；其他平台交给 Tabbit 界面流程')
    scan.add_argument('--plan',required=True);scan.add_argument('--confirm',required=True,help='用户确认该计划后的摘要 ID')
    scan.add_argument('--out',required=True);scan.add_argument('--only',help='仅恢复这些平台，输出至新目录')
    report=sub.add_parser('report',help='从 Tabbit 提取的标准岗位证据生成报告，不访问网络')
    report.add_argument('--plan',required=True);report.add_argument('--input',nargs='+',required=True);report.add_argument('--statuses');report.add_argument('--out',required=True)
    report.add_argument('--verbose',action='store_true',help='输出完整报告元数据；默认仅精简回执')
    work=sub.add_parser('worklist',help='检查所有平台的数量与待办；不能只做两个自动采集平台就交付')
    work.add_argument('--plan',required=True);work.add_argument('--input',nargs='+',default=[]);work.add_argument('--statuses');work.add_argument('--out',required=True)
    d=sub.add_parser('demo',help='生成明确标注的虚构示例，用于验收 HTML');d.add_argument('--out',required=True)
    d.add_argument('--verbose',action='store_true')
    args=ap.parse_args()
    if args.cmd=='platforms':
        print(json.dumps(PLATFORMS,ensure_ascii=False,indent=2));return 0
    if args.cmd=='doctor':
        import importlib.util
        from jobscout.browser import launcher, TabbitError
        result={'python':sys.version.split()[0],'openpyxl':bool(importlib.util.find_spec('openpyxl'))}
        try: result['tabbit_cli']=launcher()
        except TabbitError as e:result['tabbit_error']=str(e)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if result['openpyxl'] and 'tabbit_cli' in result else 2
    if args.cmd=='plan':
        if args.input and (args.keyword or args.city or args.platforms):
            raise ValueError('--input 不与快捷条件混用，以免静默覆盖')
        raw=read_json(args.input) if args.input else {'keywords':args.keyword or [],'cities':args.city or []}
        if args.platforms:raw['platforms']=args.platforms.split(',')
        if args.count is not None:
            raw['jobs_per_platform']=args.count
        if args.platform_count:
            overrides=dict(raw.get('platform_job_counts',{}))
            for value in args.platform_count:
                try:
                    platform,count=value.split('=',1);overrides[platform]=int(count)
                except ValueError:
                    raise ValueError('--platform-count 请写为平台=数量，例如 boss=10')
            raw['platform_job_counts']=overrides
        if args.auto_budget:
            raw['max_pages_per_platform']=None
            raw['max_details_per_platform']=None
        if args.pages is not None:raw['max_pages_per_platform']=args.pages
        if args.details is not None:raw['max_details_per_platform']=args.details
        p=validate_plan(raw);save(args.out,p)
        routes_file=Path(args.out).with_suffix('.routes.json')
        if routes_file == Path(args.out):
            routes_file=Path(str(args.out)+'.routes.json')
        save(routes_file,search_routes(p))
        print(json.dumps({'plan':p,'confirmation_id':digest(p),'routes_file':str(routes_file),'next':'向用户展示条件、建议方向、未知值策略与平台；收到明确确认后才 scan --confirm 此 ID。计划有变须重新确认。'},ensure_ascii=False,indent=2));return 0
    if args.cmd=='demo':
        print(json.dumps(report_receipt(demo(args.out),args.out,args.verbose),ensure_ascii=False,indent=2));return 0
    p=plan_at(args.plan)
    if args.cmd=='routes':
        if Path(args.out).expanduser().resolve()==Path(args.plan).expanduser().resolve():
            raise ValueError('路线输出不能覆盖输入计划；请指定不同文件')
        save(args.out,search_routes(p))
        print(json.dumps({'out':str(Path(args.out).expanduser().resolve()),'plan_digest':digest(p),'platforms':p['platforms']},ensure_ascii=False));return 0
    if args.cmd in ('report','worklist'):
        jobs=[]
        for file in args.input:
            raw=read_json(file)
            if not isinstance(raw,list):raise ValueError('输入岗位必须为 JSON 数组')
            jobs.extend(raw)
        statuses=validate_statuses(read_json(args.statuses),p) if args.statuses else {}
        for k in p['platforms']:
            statuses.setdefault(k,{'status':'imported' if any(j.get('platform')==k for j in jobs) else 'not_scanned','note':'仅有导入证据，未声明搜索覆盖范围','url':PLATFORMS[k]['home']})
        ranked=build_report(jobs,p)
        if args.cmd=='worklist':
            actions=write_actions(args.out,p,ranked,statuses)
            print(json.dumps(actions,ensure_ascii=False,indent=2))
            return 0 if actions['can_deliver_final'] else 3
        meta=write_report(ranked,p,statuses,args.out)
        print(json.dumps(report_receipt(meta,args.out,args.verbose),ensure_ascii=False,indent=2))
        return 0 if meta['targets']['can_deliver_final'] else 3
    if args.cmd=='scan' and args.confirm!=digest(p):
        raise ValueError('确认 ID 与当前计划不符；先展示新计划并取得用户确认，不能沿用旧确认')
    selected=args.only.split(',') if args.only else p['platforms']
    if not selected or len(selected)!=len(set(selected)) or any(k not in p['platforms'] for k in selected):
        raise ValueError('--only 只能指定计划中不重复的平台')
    # Import after confirmation; offline operations never require Tabbit.
    from jobscout import browser
    with browser.run_lock():
        return run_browser(args,p,selected)


def run_browser(args,p,selected):
    from jobscout import browser
    browser.launcher()
    out=Path(args.out).expanduser();out.mkdir(parents=True,exist_ok=True)
    if args.cmd=='scan' and (out/'raw-jobs.json').exists():
        raise ValueError('此目录已有采集结果，请使用新目录；通过 report 合并，避免覆盖之前的岗位')
    task='Job Search '+dt.datetime.now().strftime('%m%d-%H%M%S')
    statuses={};all_jobs=[];transport_failed=False
    save(out/'search-routes.json',search_routes(p))
    print('本轮平台与目标：'+ '；'.join(f'{PLATFORMS[k]["name"]} {target_count(p,k)} 个' for k in selected),flush=True)
    try:
        for index,platform in enumerate(selected):
            if index:time.sleep(random.uniform(5,8))
            try:
                state=browser.inspect(task,platform,entry_url=query_routes(p,platform)[0]['entry_url']) if platform=='bendibao' else browser.inspect(task,platform)
                if state.get('login')=='signed_out' and state['status'] in ('ready','needs_inspection'):
                    state['status']='login_required'
                statuses[platform]={**state,'jobs':0}
                print(f'{PLATFORMS[platform]["name"]}: {state["status"]} / 登录状态 {state["login"]}',flush=True)
                if state['status']=='login_required':
                    message=login_prompt(platform,state)
                    statuses[platform].update(note=message,requires_user_action=True)
                    print('\n【需要用户登录】'+message+'\n',flush=True)
                elif state.get('login')=='unknown':
                    print(f'【访问检查】{PLATFORMS[platform]["name"]} 身份未知；公开页面可继续读取，只有实际登录墙才提示登录。',flush=True)
                if args.cmd=='preflight':continue
                blockers=('login_required','blocked','app_required','wrong_context','browser_busy','access_denied','model_rate_limited','host_rate_limited','platform_rate_limited')
                # Automatic platforms: a scratch inspection page blanked by the
                # platform (browser_error/needs_inspection) must not block the
                # collector — the search response arrives before the blank window.
                if state['status'] in blockers or (PLATFORMS[platform]['mode']=='guided' and state['status']!='ready'):
                    statuses[platform].setdefault('note','尚未完成页面核实；按 next-actions.json 区分 Agent 排查与确需用户操作。')
                    continue
                if PLATFORMS[platform]['mode']=='guided':
                    note=f'必须由 Agent 继续 Tabbit 界面搜索，目标 {target_count(p,platform)} 个；不能跳过此平台后宣称完成。见 next-actions.json 与 references/platforms.md。'
                    statuses[platform].update(status='guided_pending',note=note)
                    print(f'【必须继续】{PLATFORMS[platform]["name"]}：{note}',flush=True)
                    continue
                queries=[];cooldown=False
                pairs=[(kw,city) for kw in p['keywords'] for city in p['cities']]
                for idx,(kw,city) in enumerate(pairs):
                    remaining=p['max_pages_per_platform']-sum(q['pages'] for q in queries)
                    if remaining <= 0:break
                    if idx:time.sleep(random.uniform(5,8))
                    # Cover each confirmed query once, then let worklist choose
                    # detail verification before spending more pages. Larger
                    # total budgets must not become one long browser program.
                    budget=1
                    # Reuse only a genuine user-opened tab (it is not blanked);
                    # inspection scratch pages are ours and stay disposable.
                    res=browser.scan(task,platform,kw,city,budget,
                        tab_id=state.get('tab_id') if state.get('reused_tab') else None)
                    used=max(1,res.get('pages',0))
                    if res.get('immediate') and remaining > used:
                        # Page blanked before any evidence arrived: one slow retry on
                        # another disposable page; a second instant blank is the
                        # platform's anti-automation escalation, not a login problem.
                        time.sleep(random.uniform(5,8))
                        retry=browser.scan(task,platform,kw,city,budget)
                        used+=max(1,retry.get('pages',0))
                        if not retry.get('immediate'):
                            res=retry
                        else:
                            queries.append({'keyword':kw,'city':city,'status':'anti_bot_cooldown','pages':used})
                            cooldown=True
                            break
                    queries.append({'keyword':kw,'city':city,'status':res['status'],'pages':used})
                    all_jobs.extend(res.get('jobs',[]))
                    if res['status'] not in ('ok','empty'):break
                if cooldown:
                    count=sum(j['platform']==platform for j in all_jobs)
                    statuses[platform].update(status='anti_bot_cooldown',jobs=count,queries=queries,
                        note='平台把自动化页面立即置为 about:blank（反自动化反制），两次一次性新页均未捕获响应。这不是未登录，不需要用户操作。停止读取与刷新，保留已捕获证据；建议等待 20-30 分钟冷却后用 scan --only 恢复本平台，不从头重扫。')
                    print(f'【冷却】{PLATFORMS[platform]["name"]}：页面被平台立即置空，进入冷却等待；已捕获 {count} 条保留。',flush=True)
                    continue
                good=len(queries)==len(pairs) and all(q['status'] in ('ok','empty') for q in queries)
                count=sum(j['platform']==platform for j in all_jobs)
                statuses[platform].update(status=('ok' if count else 'empty') if good else 'partial',jobs=count,queries=queries,
                    details_attempted=0,details_read=0,detail_urls=[],
                    note='已读取页面证据；只覆盖本轮有限页数。' if good else '部分查询未完成，请核对 queries 中的状态；勿把缺失当零结果。')
                if not good and res['status'] in ('platform_rate_limited','access_denied','login_required','blocked','browser_error'):
                    statuses[platform]['status']=res['status']
                if good and not count:
                    statuses[platform]['stop_reason']='results_exhausted'
            except (browser.TabbitError, ValueError) as e:
                statuses[platform]={'status':getattr(e,'code','browser_error'),'jobs':0,'url':PLATFORMS[platform]['home'],'note':str(e)}
                print(f'{platform}: {e}',file=sys.stderr,flush=True)
                if isinstance(e,browser.TabbitError):
                    transport_failed=True;break
            finally:
                save(out/'platform-status.json',statuses);save(out/'raw-jobs.json',all_jobs)
    finally:
        try:browser.finish(task)
        except browser.TabbitError as e:print(f'任务释放未确认：{e}',file=sys.stderr)
    for k in p['platforms']:
        statuses.setdefault(k,{'status':'not_scanned','jobs':0,'note':'本轮未搜索','url':PLATFORMS[k]['home']})
    save(out/'platform-status.json',statuses)
    actions=write_actions(out,p,build_report(all_jobs,p),statuses)
    if args.cmd=='scan':
        write_report(build_report(all_jobs,p),p,statuses,out)
        if not actions['complete']:
            print('本轮尚未完成每平台数量目标；必须读取 next-actions.json，提示用户登录或继续 Tabbit 搜索。',flush=True)
        for row in actions['platforms']:
            print(f'{row["name"]}：符合 {row["qualified"]}/{row["target"]}，待核实 {row["review"]}，下一步 {row["action"]}。',flush=True)
    print(str(out.resolve()),flush=True)
    return 2 if transport_failed else 3 if (args.cmd=='scan' and not actions['complete']) or any(s['status'] not in ('ok','empty','ready') for s in statuses.values()) else 0

def cli():
    try:
        return main()
    except (ValueError, OSError, RuntimeError) as e:
        print('错误：'+str(e),file=sys.stderr)
        return 2

if __name__=='__main__':
    sys.exit(cli())
