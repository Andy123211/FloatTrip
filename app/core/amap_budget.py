"""Optional durable Amap request budget for the isolated interview demo."""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlsplit, parse_qsl, urlencode


class AmapBudgetExhausted(RuntimeError):
    pass


def unlimited():
    return os.getenv('AMAP_BUDGET_UNLIMITED', '0').lower() in ('1', 'true', 'yes')


def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.execute('CREATE TABLE IF NOT EXISTS requests (id INTEGER PRIMARY KEY, at REAL, url TEXT, phase TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS cache_hits (id INTEGER PRIMARY KEY, at REAL, evidence TEXT)')
    return db


def status(path=None):
    path = path or os.getenv('AMAP_BUDGET_DB')
    if not path:
        return None
    with closing(connect(path)) as db:
        used = db.execute('SELECT count(*) FROM requests').fetchone()[0]
        hits = db.execute('SELECT count(*) FROM cache_hits').fetchone()[0]
    limit = None if unlimited() else 50
    return {'used': used, 'limit': limit, 'remaining': None if limit is None else max(0, limit-used),
            'cache_hits': hits, 'unlimited': limit is None,
            'phase_limit': None if limit is None else min(50, int(os.getenv('AMAP_BUDGET_LIMIT', '30')))}


def reserve(url):
    path = os.getenv('AMAP_BUDGET_DB')
    parsed = urlsplit(str(url))
    if not path or parsed.hostname != 'restapi.amap.com':
        return
    # Never persist credentials; count every network attempt, including retries.
    public = [(k, v) for k, v in parse_qsl(parsed.query) if k.lower() not in
              {'key', 'api_key', 'token', 'access_token', 'sig'}]
    safe = parsed.path + '?' + urlencode(sorted(public))
    limit = None if unlimited() else min(50, int(os.getenv('AMAP_BUDGET_LIMIT', '30')))
    with closing(connect(path)) as db:
        db.execute('BEGIN IMMEDIATE')
        if limit is not None and db.execute('SELECT count(*) FROM requests').fetchone()[0] >= limit:
            db.rollback()
            raise AmapBudgetExhausted('演示高德联网预算已达上限；可继续查看历史行程和有效缓存。')
        db.execute('INSERT INTO requests(at,url,phase) VALUES(?,?,?)',
                   (time.time(), safe, os.getenv('DEMO_PHASE', 'rehearsal')))
        db.commit()


def record_cache_hit(evidence):
    path = os.getenv('AMAP_BUDGET_DB')
    if not path:
        return
    import json
    with closing(connect(path)) as db:
        db.execute('INSERT INTO cache_hits(at,evidence) VALUES(?,?)',
                   (time.time(), json.dumps(evidence, ensure_ascii=False)))
        db.commit()
