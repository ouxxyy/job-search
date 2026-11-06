"""Pure plan validation, evidence handling and shortlist ranking (no network)."""
from __future__ import annotations
import copy
import datetime as dt
import hashlib
import json
import math
import re
from urllib.parse import urlsplit, urlunsplit

PLATFORMS = {
    'boss': {'name': 'BOSS直聘', 'home': 'https://www.zhipin.com/', 'mode': 'automatic', 'host': 'zhipin.com'},
    'liepin': {'name': '猎聘', 'home': 'https://www.liepin.com/', 'mode': 'automatic', 'host': 'liepin.com'},
    'maimai': {'name': '脉脉', 'home': 'https://maimai.cn/', 'mode': 'guided', 'host': 'maimai.cn'},
    'zhaopin': {'name': '智联招聘', 'home': 'https://www.zhaopin.com/', 'mode': 'guided', 'host': 'zhaopin.com'},
    '51job': {'name': '前程无忧', 'home': 'https://www.51job.com/', 'mode': 'guided', 'host': '51job.com'},
    'yupao': {'name': '鱼泡直聘', 'home': 'https://www.yupao.com/', 'mode': 'guided', 'host': 'yupao.com'},
    'shixiseng': {'name': '实习僧', 'home': 'https://www.shixiseng.com/', 'mode': 'guided', 'host': 'shixiseng.com'},
    'bendibao': {'name': '本地宝', 'home': 'https://www.bendibao.com/', 'mode': 'guided', 'host': 'bendibao.com', 'listing_check': True},
    'guopin': {'name': '国聘', 'home': 'https://www.iguopin.com/', 'mode': 'guided', 'host': 'iguopin.com', 'listing_check': True},
    'public_jobs': {'name': '中国公共招聘网', 'home': 'http://job.mohrss.gov.cn/', 'mode': 'guided', 'host': 'job.mohrss.gov.cn', 'listing_check': True},
    'ncss': {'name': '国家大学生就业服务平台', 'home': 'https://www.ncss.cn/', 'mode': 'guided', 'host': 'ncss.cn', 'listing_check': True},
}
DEFAULTS = {
    'version': 1, 'mode': 'custom', 'keywords': [], 'cities': [],
    'platforms': ['boss', 'liepin', 'zhaopin', '51job', 'maimai', 'bendibao'],
    'salary_min_k': None, 'salary_max_k': None, 'fresh_days': 60,
    'active_days': 7, 'unknown_policy': 'review', 'require_active': False,
    'must_keywords': [], 'plus_keywords': [], 'avoid_keywords': [],
    'experience_years': None, 'education': None, 'employment_types': [],
    'industries': [], 'company_sizes': [], 'extra_requirements': [],
    'suggested_directions': [], 'max_pages_per_platform': None,
    'jobs_per_platform': 5, 'platform_job_counts': {},
    'max_details_per_platform': None, 'shortlist_limit': None,
}
LIST_FIELDS = ('keywords', 'cities', 'platforms', 'must_keywords', 'plus_keywords',
               'avoid_keywords', 'employment_types', 'industries', 'company_sizes', 'extra_requirements')
EDUCATION = {'初中': 0, '高中': 1, '中专': 1, '大专': 2, '本科': 3, '硕士': 4, '博士': 5}

def validate_plan(raw):
    if not isinstance(raw, dict):
        raise ValueError('计划必须是 JSON 对象')
    extra = set(raw) - set(DEFAULTS)
    if extra:
        raise ValueError('未知计划字段：' + ', '.join(sorted(extra)))
    p = copy.deepcopy(DEFAULTS)
    p.update(raw)
    for k in LIST_FIELDS:
        if not isinstance(p[k], list) or any(not isinstance(v, str) or not v.strip() for v in p[k]):
            raise ValueError(f'{k} 必须是非空字符串数组（可以为空数组）')
        p[k] = list(dict.fromkeys(v.strip() for v in p[k]))
    if not p['keywords'] or not p['cities'] or not p['platforms']:
        raise ValueError('必须确认 keywords、cities、platforms；全国请显式填写“全国”')
    if any(x not in PLATFORMS for x in p['platforms']):
        raise ValueError('不支持的平台；运行 platforms 查看支持列表')
    if p['version'] != 1 or p['mode'] not in ('custom', 'resume'):
        raise ValueError('version 必须是 1，mode 必须是 custom / resume')
    if p['unknown_policy'] not in ('review', 'exclude'):
        raise ValueError('unknown_policy 必须是 review / exclude')
    if type(p['require_active']) is not bool:
        raise ValueError('require_active 必须是布尔值')
    for k, lo, hi in [('fresh_days', 1, 365), ('active_days', 1, 60), ('jobs_per_platform', 1, 100)]:
        if type(p[k]) is not int or not lo <= p[k] <= hi:
            raise ValueError(f'{k} 必须为 {lo}–{hi} 的整数')
    overrides = p['platform_job_counts']
    if not isinstance(overrides, dict) or any(k not in p['platforms'] for k in overrides):
        raise ValueError('platform_job_counts 必须是本轮平台到数量的映射')
    if any(type(n) is not int or not 1 <= n <= 100 for n in overrides.values()):
        raise ValueError('每个平台的岗位数须为 1–100 的整数')
    # Old global cap is retained only as a derived field for v0.2.0 plan compatibility.
    # It must never let one platform consume another platform's quota.
    p['shortlist_limit'] = sum(target_count(p, k) for k in p['platforms'])
    pairs = len(p['keywords']) * len(p['cities'])
    largest_target = max(target_count(p, k) for k in p['platforms'])
    # Resolve defaults only for new/unset budgets. Persisted plans and their
    # confirmation digests must retain the exact limits the user approved.
    if p['max_pages_per_platform'] is None:
        p['max_pages_per_platform'] = max(6, 2 * pairs, 3 * math.ceil(largest_target / 5))
    if p['max_details_per_platform'] is None:
        p['max_details_per_platform'] = max(30, 6 * largest_target)
    for k, hi in [('max_pages_per_platform', 60), ('max_details_per_platform', 600)]:
        if type(p[k]) is not int or not 1 <= p[k] <= hi:
            raise ValueError(f'{k} 须为 1–{hi} 的整数；超大搜索请拆轮，不静默截断')
    if pairs > p['max_pages_per_platform']:
        raise ValueError('关键词 × 城市数量超过每平台总页预算；展示更高预算或拆轮后确认，不能暗中漏搜或超预算')
    for k in ('salary_min_k', 'salary_max_k', 'experience_years'):
        n = p[k]
        if n is not None and (type(n) not in (int, float) or not math.isfinite(n) or n < 0):
            raise ValueError(f'{k} 必须是非负有限数或 null')
    if p['salary_min_k'] is not None and p['salary_max_k'] is not None and p['salary_min_k'] > p['salary_max_k']:
        raise ValueError('薪资下限不能高于上限')
    if p['education'] is not None and p['education'] not in EDUCATION:
        raise ValueError('education 请用初中/高中/中专/大专/本科/硕士/博士或 null')
    if not isinstance(p['suggested_directions'], list):
        raise ValueError('suggested_directions 必须是数组')
    for s in p['suggested_directions']:
        if not isinstance(s, dict) or set(s) != {'title', 'evidence', 'gap', 'accepted'}:
            raise ValueError('方向建议须有 title/evidence/gap/accepted')
        if any(not isinstance(s[k], str) or not s[k].strip() for k in ('title', 'evidence', 'gap')) or type(s['accepted']) is not bool:
            raise ValueError('方向建议需要真实证据、差距说明和明确接受状态')
        if (s['title'] in p['keywords']) != s['accepted']:
            raise ValueError('仅已接受的方向可进入 keywords；接受后须加入 keywords')
    return p

def digest(p):
    return hashlib.sha256(json.dumps(p, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:16]

def target_count(plan, platform):
    return plan['platform_job_counts'].get(platform, plan['jobs_per_platform'])

def age_days(value, today=None):
    """Do not interpret refresh times, unlabelled numeric values or future dates as new posts."""
    today = today or dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()
    s = str(value or '').strip()
    if not s:
        return None
    if re.search(r'刚刚|今天|今日|\d+\s*(分钟|小时)前', s):
        return 0
    if '昨天' in s:
        return 1
    if '前天' in s:
        return 2
    m = re.search(r'(\d+)\s*(天|周|个月|月)前', s)
    if m:
        return int(m[1]) * {'天': 1, '周': 7, '个月': 30, '月': 30}[m[2]]
    try:
        if re.fullmatch(r'\d{10}|\d{13}', s):
            date = dt.datetime.fromtimestamp(int(s) / (1000 if len(s) == 13 else 1), dt.timezone(dt.timedelta(hours=8))).date()
        else:
            m = re.search(r'(20\d\d)[年/.-](\d{1,2})[月/.-](\d{1,2})', s)
            if not m:
                return None
            date = dt.date(*map(int, m.groups()))
        days = (today - date).days
        return days if days >= 0 else None
    except (ValueError, OverflowError, OSError):
        return None

def salary_range(text):
    """Monthly K only. Never invent the upper end, annual bonus or days worked."""
    s = str(text or '').lower().replace(',', '').replace('－', '-').replace('—', '-')
    if re.search(r'日|天|时|次|周|年', s):
        return None
    m = re.search(r'(\d+(?:\.\d+)?)\s*([k千万元]?)\s*[-~至]\s*(\d+(?:\.\d+)?)\s*([k千万元])', s)
    if m:
        factor = {'k': 1, '千': 1, '万': 10, '元': .001}
        lo, hi = float(m[1]) * factor[m[2] or m[4]], float(m[3]) * factor[m[4]]
        return (lo, hi) if lo <= hi else None
    m = re.search(r'(\d+(?:\.\d+)?)\s*([k千万元])', s)
    if m and not re.search(r'以上|以下|起', s):
        n = float(m[1]) * {'k': 1, '千': 1, '万': 10, '元': .001}[m[2]]
        return n, n
    return None

def safe_url(value, platform=None):
    try:
        u = urlsplit(str(value or ''))
        if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password:
            return ''
        if platform:
            host = PLATFORMS[platform]['host']
            if not (u.hostname == host or u.hostname.endswith('.' + host)):
                return ''
        return urlunsplit((u.scheme, u.netloc, u.path, u.query, ''))
    except (ValueError, KeyError):
        return ''

STR_FIELDS = ('title', 'company', 'salary', 'city', 'district', 'experience', 'degree',
              'industry', 'scale', 'employment_type', 'description', 'publish_time', 'refresh_time',
              'hr_active', 'hr_reply', 'link', 'source_url', 'captured_at', 'evidence', 'id',
              'source_kind', 'source_publish_time', 'original_url', 'original_evidence', 'original_captured_at',
              'deadline', 'deadline_at', 'recruitment_status', 'recruitment_evidence',
              'eligibility_status', 'eligibility_evidence')

def deadline_end(value):
    """Only explicit ISO dates/times; a date-only deadline includes that local day."""
    tz = dt.timezone(dt.timedelta(hours=8))
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return dt.datetime.combine(dt.date.fromisoformat(value), dt.time.max, tzinfo=tz)
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError('deadline_at 时间必须包含时区')
    return parsed

def normalize_job(raw):
    if not isinstance(raw, dict) or raw.get('platform') not in PLATFORMS:
        raise ValueError('岗位缺少有效 platform')
    j = {'platform': raw['platform']}
    for key in STR_FIELDS:
        val = raw.get(key, '')
        if val is None:
            val = ''
        if not isinstance(val, (str, int, float)):
            raise ValueError(f'岗位 {key} 必须为文本')
        j[key] = str(val)[:16000]
    if not j['title'] or not j['company']:
        raise ValueError('岗位必须有 title 和 company；不能把整页文本当成岗位')
    for k in ('skills', 'labels'):
        val = raw.get(k, [])
        if not isinstance(val, list) or any(not isinstance(x, str) for x in val):
            raise ValueError(f'{k} 必须为字符串数组')
        j[k] = val[:30]
    for k in ('link', 'source_url'):
        if j[k] and not safe_url(j[k], j['platform']):
            raise ValueError(f'{k} 必须是对应平台的 HTTP(S) 链接')
    # An originating employer/authority may be outside the discovery platform.
    # This explicit provenance field never relaxes source_url/link validation.
    if j['original_url'] and not safe_url(j['original_url']):
        raise ValueError('original_url 必须是 HTTP(S) 原始公告链接')
    for field, default, choices in (
            ('source_kind', 'job', ('job', 'announcement')),
            ('recruitment_status', 'unknown', ('unknown', 'open', 'closed', 'not_started')),
            ('eligibility_status', 'unknown', ('unknown', 'matched', 'unmatched'))):
        j[field] = j[field] or default
        if j[field] not in choices:
            raise ValueError(f'{field} 不支持此取值')
    if j['recruitment_status'] != 'unknown' and not j['recruitment_evidence'].strip():
        raise ValueError('招聘状态需要 recruitment_evidence 原文')
    if j['eligibility_status'] != 'unknown' and not j['eligibility_evidence'].strip():
        raise ValueError('报考资格判断需要 eligibility_evidence 对照依据')
    if j['original_captured_at']:
        try:
            if dt.datetime.fromisoformat(j['original_captured_at']).tzinfo is None:
                raise ValueError()
        except ValueError:
            raise ValueError('original_captured_at 必须是带时区 ISO 日期')
    if j['deadline_at']:
        try:
            deadline_end(j['deadline_at'])
        except ValueError:
            raise ValueError('deadline_at 必须为 YYYY-MM-DD 或带时区 ISO 时间')
        if not j['deadline'].strip():
            raise ValueError('deadline_at 需要 deadline 中的报名截止原文依据')
    if not j['source_url'] or not j['captured_at'] or not j['evidence']:
        raise ValueError('岗位需要 source_url、captured_at、evidence 原文依据')
    try:
        captured = dt.datetime.fromisoformat(j['captured_at'])
        if captured.tzinfo is None:
            raise ValueError()
    except ValueError:
        raise ValueError('captured_at 必须是带时区 ISO 日期')
    j['salary_display'] = j['salary'] or '薪资未披露'
    identity = j['id'] or j['link'] or j['company'] + j['title'] + j['city']
    if j['source_kind'] == 'announcement' and not j['id']:
        identity = '|'.join((j['link'], j['company'], j['title'], j['city']))
    j['key'] = hashlib.sha256((j['platform'] + '|' + identity).encode()).hexdigest()[:18]
    return j

def evidence_age(value, captured_at):
    captured = dt.datetime.fromisoformat(captured_at).astimezone(dt.timezone(dt.timedelta(hours=8))).date()
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()
    if captured > today:
        return None
    at_capture = age_days(value, captured)
    return None if at_capture is None else at_capture + (today - captured).days

def rank_job(j, p):
    j = copy.deepcopy(j)
    reasons, unknown, rejected = [], [], []
    score = 0
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    if j['recruitment_status'] == 'closed':
        rejected.append('招聘已结束或岗位已下架')
    elif j['recruitment_status'] == 'not_started':
        unknown.append('报名尚未开始')
    elif PLATFORMS[j['platform']].get('listing_check') and j['recruitment_status'] == 'unknown':
        unknown.append('当前招聘状态待核实')
    if j['deadline_at'] and deadline_end(j['deadline_at']) < now:
        rejected.append('报名截止时间已过')
    elif j['deadline'] and not j['deadline_at']:
        unknown.append('报名截止时间未能确定，需人工核实')
    if j['source_kind'] == 'announcement':
        if not j['deadline']:
            unknown.append('公告报名截止时间未知')
        if not (j['original_url'] and j['original_evidence'].strip() and j['original_captured_at']):
            unknown.append('需回溯用人单位或主管部门原公告')
        elif dt.datetime.fromisoformat(j['original_captured_at']) > now:
            unknown.append('原公告采集时间在未来，需核实')
        if j['recruitment_status'] == 'unknown' and not PLATFORMS[j['platform']].get('listing_check'):
            unknown.append('当前招聘状态待核实')
        if j['eligibility_status'] == 'unknown':
            unknown.append('公告报考资格待核实（专业、届别、年龄等）')
    if j['eligibility_status'] == 'unmatched':
        rejected.append('报考资格不符合：' + j['eligibility_evidence'])
    blob = ' '.join(str(j.get(k, '')) for k in ('title', 'company', 'description', 'industry', 'employment_type')) + ' '.join(j['skills'] + j['labels'])
    blob = blob.casefold()
    if any(k.casefold() in j['title'].casefold() for k in p['keywords']):
        score += 35; reasons.append('职位名称命中已确认方向')
    else:
        unknown.append('职位名称未命中方向，需核对职责')
    for k in p['must_keywords']:
        if k.casefold() not in blob:
            if j['description']:
                rejected.append('未满足必需关键词：' + k)
            else:
                unknown.append('未读到完整职责，需核实：' + k)
    hits = [k for k in p['plus_keywords'] if k.casefold() in blob]
    if hits:
        score += min(10, len(hits) * 3); reasons.append('加分证据：' + '、'.join(hits))
    for k in p['avoid_keywords']:
        if k.casefold() in blob:
            rejected.append('命中排除项：' + k)
    if '全国' not in p['cities']:
        if not j['city']:
            unknown.append('工作城市未知')
        elif not any(c.removesuffix('市') in j['city'] for c in p['cities']):
            rejected.append('工作城市不符')
        else:
            score += 10; reasons.append('工作城市符合')
    else:
        score += 10
    sr = salary_range(j['salary'])
    j['salary_min_k'] = sr[0] if sr else None
    if p['salary_min_k'] is not None or p['salary_max_k'] is not None:
        if sr is None:
            unknown.append('薪资未披露或非可比较月薪')
        elif (p['salary_min_k'] is not None and sr[1] < p['salary_min_k']) or (p['salary_max_k'] is not None and sr[0] > p['salary_max_k']):
            rejected.append('薪资区间不相交')
        elif p['salary_min_k'] is not None and sr[0] < p['salary_min_k']:
            unknown.append('薪资区间部分低于底线，需确认可谈范围')
        else:
            score += 15; reasons.append('月薪区间符合')
    elif sr:
        score += 10; reasons.append('已披露可比较月薪')
    days = evidence_age(j['publish_time'], j['captured_at'])
    j['publish_age_days'] = days
    if days is None:
        unknown.append('发布时间未知' + ('（只有刷新时间）' if j['refresh_time'] else ''))
    elif days > p['fresh_days']:
        rejected.append(f'发布超过 {p["fresh_days"]} 天')
    else:
        score += 15; reasons.append(f'{days} 天内发布')
    active = j['hr_active']
    active_age = evidence_age('今天' if re.fullmatch(r'(刚刚)?在线|当前在线|刚刚活跃', active) else active, j['captured_at'])
    j['active_age_days'] = active_age
    if active_age is not None and active_age <= p['active_days']:
        score += 10; reasons.append('HR 近期活跃（不等于会回复）')
    elif p['require_active']:
        if active_age is None:
            unknown.append('HR 活跃时间未知')
        else:
            rejected.append('HR 活跃时间超过要求')
    else:
        reasons.append('HR 活跃未证实')
    if p['experience_years'] is not None and not re.search(r'经验不限|无需经验|应届|无经验', j['experience']):
        m = re.search(r'(\d+)\s*(?:[-~至]\s*\d+\s*)?年', j['experience'])
        if not m:
            unknown.append('经验要求未知')
        elif int(m[1]) > p['experience_years']:
            rejected.append('工作年限不足')
    if p['education'] and not re.search(r'不限', j['degree']):
        req = next((EDUCATION[k] for k in reversed(EDUCATION) if k in j['degree']), None)
        if req is None:
            unknown.append('学历要求未知')
        elif req > EDUCATION[p['education']]:
            rejected.append('学历要求不符')
    for key, field, label in [('employment_types', 'employment_type', '用工类型'), ('industries', 'industry', '行业'), ('company_sizes', 'scale', '公司规模')]:
        if p[key]:
            if not j[field]:
                unknown.append(label + '未知')
            elif not any(k.casefold() in j[field].casefold() for k in p[key]):
                rejected.append(label + '不符')
    # Free-form constraints require agent/user evidence review; never silently pass.
    unknown.extend('人工核实：' + x for x in p['extra_requirements'])
    if not j['link']:
        unknown.append('缺少直接岗位链接')
    j['score'] = min(score, 100)
    j['score_reasons'], j['unknowns'], j['rejections'] = reasons, unknown, rejected
    j['bucket'] = 'excluded' if rejected or (unknown and p['unknown_policy'] == 'exclude') else ('review' if unknown else 'shortlist')
    j['tier'] = 'A' if j['bucket'] == 'shortlist' and score >= 75 else ('B' if j['bucket'] == 'shortlist' else 'C')
    return j

def build_report(raw_jobs, p):
    latest, jobs = {}, []
    for raw in raw_jobs:
        j = normalize_job(raw)
        if j['platform'] not in p['platforms']:
            raise ValueError('输入包含计划外平台：' + j['platform'])
        old = latest.get(j['key'])
        if old is None or dt.datetime.fromisoformat(j['captured_at']) >= dt.datetime.fromisoformat(old['captured_at']):
            latest[j['key']] = j
    jobs = [rank_job(j, p) for j in latest.values()]
    jobs.sort(key=lambda j: ({'shortlist': 0, 'review': 1, 'excluded': 2}[j['bucket']], -j['score'], j['key']))
    counts = {k: 0 for k in p['platforms']}
    for j in jobs:
        if j['bucket'] == 'shortlist':
            counts[j['platform']] += 1
            if counts[j['platform']] > target_count(p, j['platform']):
                j['bucket'] = 'reserve'
    # Do not merge cross-platform roles that might differ in location/team/salary.
    grouped = {}
    for j in jobs:
        key = tuple(j[k].strip().casefold() for k in ('company', 'title', 'city'))
        grouped.setdefault(key, []).append(j)
    for group in grouped.values():
        for j in group:
            j['duplicate_hint'] = len({x['platform'] for x in group}) > 1
    return jobs
