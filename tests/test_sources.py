"""Synthetic source routing and announcement evidence regressions; no network."""
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from openpyxl import load_workbook
import job_search
from jobscout import browser
from jobscout.core import DEFAULTS, PLATFORMS, build_report, digest, normalize_job, validate_plan
from jobscout.report import write_report
from jobscout.sources import city_directory, query_routes, search_routes
from jobscout.workflow import next_actions
from test_core import job, plan, TZ


def local_job(**kw):
    return job(platform='bendibao', source_url='https://gz.bendibao.com/job/',
               link='https://gz.bendibao.com/job/example/', recruitment_status='open',
               recruitment_evidence='合成测试：页面显示正在招聘', **kw)


def announcement(**kw):
    fields = dict(source_kind='announcement', original_url='https://example.org/hiring',
                  original_evidence='合成原公告：财务分析师，上海，3天前发布。',
                  original_captured_at=dt.datetime.now(TZ).isoformat(),
                  deadline='合成测试：2099年12月31日截止',deadline_at='2099-12-31',
                  eligibility_status='matched', eligibility_evidence='合成测试：该岗位没有其他报考限制。')
    fields.update(kw)
    return local_job(**fields)


class SourceTests(unittest.TestCase):
    def test_default_adds_local_but_old_explicit_plans_stay_unchanged(self):
        p=validate_plan({'keywords':['电工'],'cities':['苏州']})
        self.assertIn('bendibao',p['platforms']);self.assertEqual(p['shortlist_limit'],30)
        old=plan(platforms=['boss','liepin','zhaopin','51job','maimai'])
        self.assertNotIn('bendibao',old['platforms']);self.assertEqual(old['shortlist_limit'],25)
        self.assertEqual(digest(old),digest(validate_plan(old)))

    def test_city_directory_uses_official_links_and_preserves_homophones(self):
        data=city_directory()
        self.assertGreater(len(data['cities']),300)
        for city,host in [('苏州','suzhou'),('宿州','su'),('台州','tz'),('泰州','taizhou'),('安顺市','anshun')]:
            row=query_routes(plan(cities=[city]),'bendibao')[0]
            self.assertEqual(row['entry_url'],f'http://{host}.bendibao.com/')
            self.assertEqual(row['city'],city)
        self.assertNotIn('/job',query_routes(plan(cities=['昆山']),'bendibao')[0]['entry_url'])

    def test_unknown_national_and_ambiguous_regions_never_guess(self):
        for city in ('全国','不存在的城市','广州天河区','广东省','海南','吉林','朝阳','兴安'):
            r=query_routes(plan(cities=[city]),'bendibao')[0]
            self.assertEqual(r['route_status'],'city_selection_required')
            self.assertEqual(r['entry_url'],'https://www.bendibao.com/')
        self.assertEqual(query_routes(plan(cities=['吉林市']),'bendibao')[0]['entry_url'],'http://jl.bendibao.com/')

    def test_all_pairs_and_new_sources_are_available_without_network(self):
        p=plan(platforms=['bendibao','guopin','public_jobs','ncss'],keywords=['电工','焊工'],cities=['广州','苏州'])
        r=search_routes(p)
        self.assertEqual(r['plan_digest'],digest(p))
        for key,rows in r['platforms'].items():
            self.assertEqual(len(rows),4)
            self.assertEqual({(x['keyword'],x['city']) for x in rows},{(k,c) for k in p['keywords'] for c in p['cities']})
            self.assertEqual(PLATFORMS[key]['mode'],'guided')

    def test_plan_and_routes_commands_are_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'plan.json';out=Path(tmp)/'routes.json'
            with patch('jobscout.browser.launcher') as launch,patch('builtins.print'):
                with patch.object(sys,'argv',['job_search.py','plan','--keyword','电工','--city','苏州','--out',str(p)]):
                    self.assertEqual(job_search.main(),0)
                with patch.object(sys,'argv',['job_search.py','routes','--plan',str(p),'--out',str(out)]):
                    self.assertEqual(job_search.main(),0)
                before=p.read_bytes()
                with patch.object(sys,'argv',['job_search.py','routes','--plan',str(p),'--out',str(p)]):
                    with self.assertRaises(ValueError):job_search.main()
                self.assertEqual(p.read_bytes(),before)
                launch.assert_not_called()
            self.assertTrue(p.with_suffix('.routes.json').is_file())
            self.assertIn('bendibao',json.loads(out.read_text())['platforms'])

    def test_new_platforms_stay_pending_and_have_city_routes(self):
        p=plan(platforms=['bendibao','guopin','public_jobs','ncss'],cities=['广州','苏州'])
        actions=next_actions(p,[],{})
        self.assertFalse(actions['can_deliver_final']);self.assertEqual(actions['needs_agent'],p['platforms'])
        self.assertEqual(actions['platforms'][0]['url'],'http://gz.bendibao.com/')
        self.assertEqual(len(actions['platforms'][0]['search_routes']),2)

    def test_preflight_uses_requested_station_and_prefers_its_existing_tab(self):
        tabs=[{'tabId':1,'url':'https://bj.bendibao.com/job/','state':'available'},
              {'tabId':2,'url':'https://m.gz.bendibao.com/job/','state':'available'}]
        with patch.object(browser,'inventory',return_value=tabs),patch.object(browser,'node',return_value={'status':'ready','login':'unknown'}) as n:
            browser.inspect('test','bendibao',entry_url='http://gz.bendibao.com/')
            self.assertEqual(n.call_args.kwargs['tab_id'],2)
            self.assertNotIn('.goto(',n.call_args.args[1])
        with patch.object(browser,'inventory',return_value=[]),patch.object(browser,'node',return_value={'status':'ready','login':'unknown'}) as n:
            browser.inspect('test','bendibao',entry_url='http://suzhou.bendibao.com/')
            self.assertIn('http://suzhou.bendibao.com/',n.call_args.args[1])
        with patch.object(browser,'inventory') as inv:
            with self.assertRaises(ValueError):browser.inspect('test','bendibao',entry_url='https://bendibao.com.evil.invalid/')
            inv.assert_not_called()

    def test_original_source_does_not_relax_platform_url_checks(self):
        self.assertEqual(normalize_job(announcement())['original_url'],'https://example.org/hiring')
        for fields in ({'original_url':'javascript:alert(1)'},{'source_url':'https://example.org/hiring'},
                       {'link':'https://bendibao.com.evil.invalid/job'},{'original_captured_at':'2026-11-06'}):
            raw=announcement();raw.update(fields)
            with self.assertRaises(ValueError):normalize_job(raw)

    def test_local_site_does_not_override_actual_work_city(self):
        p=plan(platforms=['bendibao'],cities=['广州'])
        self.assertEqual(build_report([local_job(city='佛山')],p)[0]['bucket'],'excluded')
        self.assertEqual(build_report([local_job(city='')],p)[0]['bucket'],'review')

    def test_new_boards_require_current_status_and_do_not_require_hr_presence(self):
        p=plan(platforms=['bendibao'])
        self.assertEqual(build_report([local_job(hr_active='')],p)[0]['bucket'],'shortlist')
        raw=local_job();raw['recruitment_status']='unknown'
        self.assertEqual(build_report([raw],p)[0]['bucket'],'review')
        raw['recruitment_status']='closed'
        self.assertEqual(build_report([raw],p)[0]['bucket'],'excluded')
        raw['recruitment_status']='not_started'
        self.assertEqual(build_report([raw],p)[0]['bucket'],'review')

    def test_deadline_overrides_recent_publication_and_open_label(self):
        p=plan(platforms=['bendibao']);now=dt.datetime.now(TZ)
        expired=(now-dt.timedelta(days=1)).date().isoformat()
        j=build_report([announcement(deadline='合成测试：报名截至昨天',deadline_at=expired)],p)[0]
        self.assertEqual(j['bucket'],'excluded');self.assertIn('报名截止时间已过',j['rejections'])
        j=build_report([announcement(deadline='合成测试：截至今天',deadline_at=now.date().isoformat())],p)[0]
        self.assertEqual(j['bucket'],'shortlist')
        j=build_report([announcement(deadline='合成测试：半小时前截止',deadline_at=(now-dt.timedelta(minutes=30)).isoformat())],p)[0]
        self.assertEqual(j['bucket'],'excluded')

    def test_deadline_and_status_fields_reject_unsupported_assertions(self):
        for fields in ({'deadline_at':'2026-13-01','deadline':'原文'},
                       {'deadline_at':'2026-11-30T10:00:00','deadline':'原文'},
                       {'deadline_at':'2026-11-30'}, {'recruitment_status':'active'},
                       {'recruitment_evidence':''},{'eligibility_status':'matched','eligibility_evidence':''}):
            raw=local_job();raw.update(fields)
            with self.assertRaises(ValueError):normalize_job(raw)

    def test_announcements_need_original_source_and_eligibility_evidence(self):
        p=plan(platforms=['bendibao'])
        for fields in ({'original_url':''},{'original_evidence':''},{'original_captured_at':''},
                       {'eligibility_status':'unknown'},{'deadline':'招满即止','deadline_at':''},
                       {'deadline':'','deadline_at':''},
                       {'publish_time':'','source_publish_time':'今天'}):
            self.assertEqual(build_report([announcement(**fields)],p)[0]['bucket'],'review')
        j=build_report([announcement(eligibility_status='unmatched',eligibility_evidence='合成测试：专业不符')],p)[0]
        self.assertEqual(j['bucket'],'excluded')

    def test_multiple_positions_in_one_announcement_do_not_collapse(self):
        p=plan(platforms=['bendibao'])
        a=announcement(id='');b=announcement(id='',title='高级财务分析师')
        self.assertEqual(len(build_report([a,b,a],p)),2)

    def test_original_notice_queue_uses_same_detail_budget(self):
        p=plan(platforms=['bendibao']);raw=announcement(original_evidence='')
        jobs=build_report([raw],p)
        state={'status':'ok','stop_reason':'results_exhausted','pages_read':1,
               'details_attempted':1,'details_read':1,'detail_urls':[raw['link']]}
        actions=next_actions(p,jobs,{'bendibao':state})
        row=actions['platforms'][0]
        self.assertEqual(row['action'],'verify_details')
        self.assertEqual(row['detail_queue'][0]['url'],raw['original_url'])
        self.assertEqual(row['detail_queue'][0]['source'],'original')
        state.update(original_detail_urls=[raw['original_url']],details_attempted=2)
        job_search.validate_statuses({'bendibao':state},p)
        self.assertEqual(next_actions(p,jobs,{'bendibao':state})['platforms'][0]['action'],'shortfall')
        state['details_attempted']=1
        with self.assertRaises(ValueError):job_search.validate_statuses({'bendibao':state},p)

    def test_report_preserves_original_evidence_and_excel_hyperlink(self):
        p=plan(platforms=['bendibao']);raw=announcement(original_evidence='=DANGEROUS()',deadline='合成：2099年12月31日',deadline_at='2099-12-31')
        raw['recruitment_evidence']='合成：<script>alert(1)</script>'
        with tempfile.TemporaryDirectory() as tmp:
            write_report(build_report([raw],p),p,{},tmp)
            html=(Path(tmp)/'jobs.html').read_text()
            self.assertNotIn('<script>alert(1)</script>',html)
            wb=load_workbook(Path(tmp)/'jobs.xlsx');ws=wb['优先投递']
            heads={c.value:c.column for c in ws[1]}
            self.assertEqual(ws.cell(2,heads['原始公告']).hyperlink.target,raw['original_url'])
            self.assertEqual(ws.cell(2,heads['原公告依据']).value,"'=DANGEROUS()")
            self.assertEqual(ws.cell(2,heads['截止时间标准值']).value,'2099-12-31')


if __name__=='__main__':unittest.main()
