#!/usr/bin/env python3
"""Create a redistributable ZIP from an explicit source manifest, never run outputs."""
import argparse
from pathlib import Path
import zipfile
from jobscout import __version__
ROOT=Path(__file__).resolve().parent
FILES=['SKILL.md','README.md','LICENSE','THIRD_PARTY_NOTICES.md','requirements.txt','.gitignore',
       'job_search.py','job_scout.py','install.py','build_package.py','jobscout/__init__.py','jobscout/core.py',
       'jobscout/browser.py','jobscout/report.py','jobscout/workflow.py','assets/report.html','assets/collect.js','assets/page-state.js',
       'data/city_codes_boss.json','data/city_codes_liepin.json','examples/custom-plan.json',
       'examples/resume-plan.json','references/intake.md','references/data-contract.md',
       'references/platforms.md','references/tabbit.md','references/verification.md',
       'assets/README.md','assets/demo.gif','assets/report-overview.png','assets/report-evidence.png','assets/wechat-qr.jpg',
       'references/cli.md','tests/test_core.py','tests/test_cli.py','tests/test_browser_session.py','tests/test_page_state.cjs','tests/test_workflow.py','tests/test_rename.py','tests/test_transport.py','tests/test_collect.cjs',
       'tests/test_budget.py','references/budget.md','.github/workflows/tests.yml',
       'jobscout/sources.py','data/bendibao_cities.json','tests/test_sources.py',
       'references/local-sources.md','references/source-verification.md','examples/local-plan.json',
       '.claude-plugin/plugin.json']

def release_files():
    paths=[ROOT/p for p in FILES]
    for p in paths:
        if not p.is_file() or p.is_symlink():raise ValueError('缺失或不是普通发布文件：'+str(p))
    return paths

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--out',required=True);a=ap.parse_args()
    out=Path(a.out).expanduser();out.mkdir(parents=True,exist_ok=True)
    target=out/f'job-search-{__version__}.zip'
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as z:
        for p in release_files():z.write(p,'job-search/'+str(p.relative_to(ROOT)))
    print(target.resolve())
