import contextlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import job_search
import job_scout
from install import install, ROOT
from jobscout import browser

class RenameCompatibilityTests(unittest.TestCase):
    def test_legacy_import_is_same_module(self):
        self.assertIs(job_scout, job_search)
        self.assertIs(job_scout.main, job_search.main)

    def test_new_and_legacy_cli_keep_plan_and_confirmation_identical(self):
        with tempfile.TemporaryDirectory() as d:
            values=[]
            for script in ['job_search.py','job_scout.py']:
                out=Path(d)/(script+'.json')
                p=subprocess.run([sys.executable,str(ROOT/script),'plan','--keyword','财务分析师','--city','上海','--out',str(out)],cwd=d,capture_output=True,text=True)
                self.assertEqual(p.returncode,0,p.stderr)
                receipt=json.loads(p.stdout)
                self.assertTrue(Path(receipt.pop('routes_file')).is_file())
                values.append(receipt)
            self.assertEqual(values[0],values[1])

    def test_both_cli_reject_wrong_confirmation_before_browser(self):
        with tempfile.TemporaryDirectory() as d:
            from jobscout.core import validate_plan
            plan=Path(d)/'plan.json'
            plan.write_text(json.dumps(validate_plan({'keywords':['财务分析师'],'cities':['上海']})))
            for module,script in [(job_search,'job_search.py'),(job_scout,'job_scout.py')]:
                with self.subTest(script=script),patch.object(sys,'argv',[script,'scan','--plan',str(plan),'--confirm','wrong','--out',d]),patch('jobscout.browser.launcher') as launch:
                    with self.assertRaises(ValueError):module.main()
                    launch.assert_not_called()

    def test_installer_migrates_only_same_source_link(self):
        with tempfile.TemporaryDirectory() as d:
            old=Path(d)/'job-scout';old.symlink_to(ROOT,target_is_directory=True)
            new=install(d)
            self.assertEqual(new.name,'job-search')
            self.assertEqual(new.resolve(),ROOT)
            self.assertFalse(old.is_symlink())
            self.assertEqual(install(d),new)

    def test_installer_preserves_legacy_real_directory(self):
        with tempfile.TemporaryDirectory() as d:
            old=Path(d)/'job-scout';old.mkdir();(old/'keep.txt').write_text('user content')
            self.assertEqual(install(d).name,'job-search')
            self.assertEqual((old/'keep.txt').read_text(),'user content')

    def test_legacy_state_setting_and_cross_process_lock_are_shared(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'JOB_SCOUT_STATE_DIR':d}):
            with browser.run_lock():
                self.assertTrue((Path(d)/'browser.lock').exists())
                for name in ['job_search','job_scout']:
                    code='import '+name+'\nfrom jobscout.browser import run_lock\nwith run_lock(): print("UNEXPECTED")'
                    p=subprocess.run([sys.executable,'-c',code],cwd=ROOT,capture_output=True,text=True)
                    self.assertNotEqual(p.returncode,0)
                    self.assertIn('BROWSER_RUN_BUSY',p.stderr)
                    self.assertNotIn('UNEXPECTED',p.stdout)

    def test_archive_contains_both_entrypoints_and_runs_outside_checkout(self):
        with tempfile.TemporaryDirectory() as d:
            p=subprocess.run([sys.executable,str(ROOT/'build_package.py'),'--out',d],capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
            archive=Path(p.stdout.strip())
            self.assertTrue(archive.name.startswith('job-search-'))
            with zipfile.ZipFile(archive) as z:
                names=z.namelist()
                self.assertTrue(all(n.startswith('job-search/') for n in names))
                self.assertIn('job-search/job_search.py',names)
                self.assertIn('job-search/job_scout.py',names)
                self.assertIn('job-search/jobscout/sources.py',names)
                self.assertIn('job-search/data/bendibao_cities.json',names)
                self.assertFalse(any('/.venv/' in n or '/private/' in n for n in names))
                z.extractall(Path(d)/'extracted')
            base=Path(d)/'extracted/job-search'
            for script in ['job_search.py','job_scout.py']:
                result=subprocess.run([sys.executable,str(base/script),'--help'],cwd=d,capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertIn('usage: job-search',result.stdout)
            result=subprocess.run([sys.executable,str(base/'job_search.py'),'plan','--keyword','电工','--city','苏州','--out',str(Path(d)/'plan.json')],cwd=d,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            routes=json.loads((Path(d)/'plan.routes.json').read_text())
            self.assertEqual(routes['platforms']['bendibao'][0]['entry_url'],'http://suzhou.bendibao.com/')

    def test_public_report_brand_changes_but_mark_storage_key_stays(self):
        html=(ROOT/'assets/report.html').read_text()
        self.assertIn('<title>Job Search',html)
        self.assertIn('job search <span',html)
        self.assertIn("const storageKey='job-scout:'+M.plan_digest",html)
        self.assertIn("a.download='job-search-shortlist.csv'",html)

if __name__=='__main__':unittest.main()

