"""Offline routing to observed source entries; never invent city URLs or filters."""
import json
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit
from .core import PLATFORMS, digest


@lru_cache(maxsize=1)
def city_directory():
    return json.loads((Path(__file__).resolve().parent.parent / 'data/bendibao_cities.json').read_text(encoding='utf-8'))


def query_routes(plan, platform):
    """Every confirmed keyword × city remains a separate, budgeted query."""
    config = PLATFORMS[platform]
    routes = []
    for city in plan['cities']:
        entry = config['home']
        status = 'platform_entry'
        note = '从当前可见职位搜索控件选择城市；不猜查询参数或城市码。'
        if platform == 'bendibao':
            # Do not resolve ambiguous province/district names by substring.
            # 海南 in the directory means a Qinghai prefecture, not Hainan province.
            key = city.removesuffix('市')
            entry = city_directory()['cities'].get(key) if city not in ('全国', '海南', '吉林', '朝阳', '兴安') else None
            status = 'city_home' if entry else 'city_selection_required'
            entry = entry or config['home']
            note = ('城市首页来自官方导航；先核对站点地区，再从可见“招聘/职位/招聘资讯”入口搜索。'
                    if status == 'city_home' else
                    '全国、地区有歧义或目录未收录：在官方城市导航确认，不猜子域名，不擅自改搜邻近城市。')
        for keyword in plan['keywords']:
            host = urlsplit(entry).hostname
            # Include both desktop and mobile station hosts in discovery hints.
            domain = host if platform == 'bendibao' and status == 'city_home' else config['host']
            routes.append({'keyword': keyword, 'city': city, 'entry_url': entry,
                           'route_status': status, 'note': note,
                           'discovery_query': f'site:{domain} {city} {keyword} 招聘'})
    return routes


def search_routes(plan):
    return {'plan_digest': digest(plan), 'note': '仅生成入口与查询路线，不访问网络，不表示已完成搜索。',
            'platforms': {key: query_routes(plan, key) for key in plan['platforms']}}
