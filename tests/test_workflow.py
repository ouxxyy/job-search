import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import job_scout
from jobscout.core import PLATFORMS, validate_plan, build_report, digest, target_count
from jobscout.workflow import next_actions
from test_core import job, plan


def jobs_for(platform, number, **kw):
    home=PLATFORMS[platform]['home']
    if PLATFORMS[platform].get('listing_check'):
        kw={'recruitment_status':'open','recruitment_evidence':'合成测试：页面显示正在招聘',**kw}
    return [job(platform=platform,id=f'{platform}-{i}',source_url=home,link=home+'job/'+str(i),**kw) for i in range(number)]

class WorkflowTests(unittest.TestCase):
    def test_default_six_platforms_five_each(self):
        p=validate_plan({'keywords':['采购专员'],'cities':['苏州']})
        self.assertEqual(set(p['platforms']),{'boss','liepin','maimai','zhaopin','51job','bendibao'})
        a=next_actions(p,[],{})
        self.assertEqual(a['target_total'],30)
        self.assertEqual(len(a['needs_agent']),6)
        self.assertTrue(all(r['target']==5 for r in a['platforms']))
        self.assertFalse(a['can_deliver_final'])
    def test_platform_quotas_independent(self):
        p=plan()
        rows=build_report(jobs_for('boss',15)+jobs_for('liepin',7),p)
        self.assertEqual(sum(j['bucket']=='shortlist' and j['platform']=='boss' for j in rows),5)
        self.assertEqual(sum(j['bucket']=='shortlist' and j['platform']=='liepin' for j in rows),5)
        self.assertEqual(sum(j['bucket']=='reserve' for j in rows),12)
    def test_custom_counts_override(self):
        p=plan(jobs_per_platform=8,platform_job_counts={'boss':10})
        self.assertEqual(target_count(p,'boss'),10);self.assertEqual(target_count(p,'liepin'),8)
        self.assertEqual(p['shortlist_limit'],18)
        self.assertEqual(p['max_details_per_platform'],60)
    def test_old_total_does_not_steal_other_platform_quota(self):
        p=plan(jobs_per_platform=20,shortlist_limit=30)
        self.assertEqual(p['shortlist_limit'],40)
        rows=build_report(jobs_for('boss',22)+jobs_for('liepin',22),p)
        self.assertEqual(sum(j['bucket']=='shortlist' for j in rows),40)
    def test_count_validation(self):
        for changes in ({'jobs_per_platform':0},{'jobs_per_platform':True},{'jobs_per_platform':101},{'platform_job_counts':{'maimai':3}},{'platform_job_counts':{'boss':0}},{'max_details_per_platform':0}):
            with self.assertRaises(ValueError):plan(**changes)
    def test_review_does_not_fill_quota(self):
        p=plan();rows=build_report(jobs_for('boss',3)+jobs_for('liepin',10,publish_time=''),p)
        a=next_actions(p,rows,{'boss':{'status':'ok'},'liepin':{'status':'ok'}})
        self.assertEqual(a['qualified_total'],3);self.assertEqual(a['platforms'][1]['missing'],5)
        self.assertEqual(a['platforms'][1]['review'],10);self.assertFalse(a['can_deliver_final'])
    def test_two_automatic_sources_not_completion(self):
        p=validate_plan({'keywords':['财务分析师'],'cities':['上海']})
        rows=build_report(jobs_for('boss',5)+jobs_for('liepin',5),p)
        s={k:{'status':'ok' if k in ('boss','liepin') else 'guided_pending'} for k in p['platforms']}
        a=next_actions(p,rows,s)
        self.assertFalse(a['complete']);self.assertFalse(a['can_deliver_final'])
        self.assertEqual(set(a['needs_agent']),{'maimai','zhaopin','51job','bendibao'})
    def test_all_six_targets_met(self):
        p=validate_plan({'keywords':['财务分析师'],'cities':['上海']})
        raw=[j for k in p['platforms'] for j in jobs_for(k,5)]
        a=next_actions(p,build_report(raw,p),{k:{'status':'ok'} for k in p['platforms']})
        self.assertTrue(a['complete']);self.assertTrue(a['can_deliver_final']);self.assertEqual(a['qualified_total'],30)
    def test_only_recorded_shortfall_allows_partial_delivery(self):
        p=plan();rows=build_report(jobs_for('boss',2),p)
        s={k:{'status':'ok'} for k in p['platforms']}
        self.assertFalse(next_actions(p,rows,s)['can_deliver_final'])
        for k in s:s[k]['stop_reason']='budget_exhausted'
        self.assertFalse(next_actions(p,rows,s)['can_deliver_final'])
        for k in s:
            s[k].update(pages_read=p['max_pages_per_platform'],details_attempted=p['max_details_per_platform'])
        a=next_actions(p,rows,s)
        self.assertTrue(a['can_deliver_final']);self.assertFalse(a['complete'])
    def test_login_is_user_action_not_shortfall(self):
        p=plan();s={k:{'status':'login_required','login':'signed_out','stop_reason':'budget_exhausted'} for k in p['platforms']}
        a=next_actions(p,[],s)
        self.assertEqual(set(a['needs_user']),{'boss','liepin'});self.assertFalse(a['can_deliver_final'])
        for r in a['platforms']:
            self.assertIn('请先在 Tabbit 登录',r['message']);self.assertIn('已登录',r['message'])
    def test_count_flags_and_confirmation(self):
        with tempfile.TemporaryDirectory() as t:
            out=Path(t)/'plan.json'
            args=['job_scout.py','plan','--keyword','会计','--city','上海','--count','8','--platform-count','boss=10','--out',str(out)]
            with patch.object(sys,'argv',args),patch('builtins.print'):self.assertEqual(job_scout.main(),0)
            p=json.loads(out.read_text());self.assertEqual(p['jobs_per_platform'],8);self.assertEqual(p['platform_job_counts'],{'boss':10})
            self.assertEqual(p['shortlist_limit'],50)
    def test_signed_out_public_page_prompts_before_scan(self):
        with tempfile.TemporaryDirectory() as t:
            p=plan(platforms=['zhaopin']);path=Path(t)/'plan.json';path.write_text(json.dumps(p))
            args=['job_scout.py','scan','--plan',str(path),'--confirm',digest(p),'--out',str(Path(t)/'out')]
            text=io.StringIO()
            with patch.object(sys,'argv',args),patch('jobscout.browser.launcher',return_value='fake'),patch('jobscout.browser.inspect',return_value={'status':'ready','login':'signed_out','url':'https://www.zhaopin.com/'}),patch('jobscout.browser.scan') as scan,patch('jobscout.browser.finish'),contextlib.redirect_stdout(text):
                self.assertEqual(job_scout.main(),3);scan.assert_not_called()
            self.assertIn('请先在 Tabbit 登录【智联招聘】',text.getvalue())
            self.assertIn('https://www.zhaopin.com/',text.getvalue())
            actions=json.loads((Path(t)/'out/next-actions.json').read_text())
            self.assertEqual(actions['needs_user'],['zhaopin'])
    def test_preflight_also_prints_login_request(self):
        with tempfile.TemporaryDirectory() as t:
            p=plan(platforms=['maimai']);path=Path(t)/'plan.json';path.write_text(json.dumps(p))
            text=io.StringIO()
            with patch.object(sys,'argv',['job_scout.py','preflight','--plan',str(path),'--out',str(Path(t)/'out')]),patch('jobscout.browser.launcher',return_value='fake'),patch('jobscout.browser.inspect',return_value={'status':'login_required','login':'signed_out'}),patch('jobscout.browser.finish'),contextlib.redirect_stdout(text):
                self.assertEqual(job_scout.main(),3)
            self.assertIn('请先在 Tabbit 登录【脉脉】',text.getvalue())

if __name__=='__main__':unittest.main()
