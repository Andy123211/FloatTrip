"""Persistent exact-request Amap cache. Stores successful public responses, never keys.

POIs: 7 days; successful empty search: 15 minutes; walking: 1 day;
traffic-sensitive routes: 15 minutes; weather: 30 minutes. Unknown APIs bypass.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit
import weakref

POLICY = 'amap-local-v1'
TTLS = {'/v3/place/text': 604800, '/v3/place/around': 604800,
        '/v3/place/detail': 604800, '/v3/direction/walking': 86400,
        '/v3/direction/transit/integrated': 900, '/v3/direction/driving': 900,
        '/v3/weather/weatherInfo': 1800}
SECRETS = {'key', 'api_key', 'token', 'access_token', 'sig'}
log = logging.getLogger(__name__)
_async_locks = weakref.WeakKeyDictionary()
_sync_locks = [threading.RLock() for _ in range(64)]


def identity(url):
    p = urlsplit(str(url))
    if p.scheme != 'https' or p.hostname != 'restapi.amap.com' or p.path not in TTLS:
        return None
    params = sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k.lower() not in SECRETS)
    canonical = 'https://restapi.amap.com' + p.path + '?' + urlencode(params)
    return hashlib.sha256(canonical.encode()).hexdigest(), canonical, p.path


def response_hash(data):
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def async_lock(url):
    loop = asyncio.get_running_loop()
    locks = _async_locks.setdefault(loop, [asyncio.Lock() for _ in range(64)])
    key = identity(url)
    return locks[int(key[0][:8], 16) % 64] if key else asyncio.Lock()


def sync_lock(url):
    key = identity(url)
    return _sync_locks[int(key[0][:8], 16) % 64] if key else threading.RLock()


class AmapCache:
    def __init__(self, path=None, clock=time.time):
        default = Path(__file__).resolve().parents[2] / 'data/amap-cache.sqlite'
        self.path = Path(path or os.getenv('AMAP_CACHE_PATH') or default)
        self.clock = clock
        self.enabled = os.getenv('AMAP_CACHE_ENABLED', '1').lower() not in ('0', 'false', 'off')

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            with conn:
                conn.execute('CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY, url TEXT NOT NULL, body TEXT NOT NULL, observed REAL NOT NULL, expires REAL NOT NULL, hash TEXT NOT NULL, source TEXT NOT NULL)')
                yield conn
        finally:
            conn.close()

    def get(self, url):
        key = identity(url)
        if not self.enabled or key is None:
            return None
        try:
            with self.connect() as conn:
                row = conn.execute('SELECT body,observed,expires,hash,source FROM responses WHERE key=? AND expires>?', (key[0], self.clock())).fetchone()
            if row:
                body = json.loads(row[0])
                if response_hash(body) != row[3]:
                    return None
                return body, {'policy': POLICY, 'request_hash': key[0], 'response_hash': row[3], 'observed_at': row[1], 'expires_at': row[2], 'source': row[4], 'cache_hit': True}
        except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
            log.warning('Amap local cache read unavailable: %s', type(exc).__name__)
        return None

    def put(self, url, data, *, observed_at=None, source='live'):
        key = identity(url)
        if not self.enabled or key is None or not isinstance(data, dict) or str(data.get('status')) != '1':
            return None
        field = 'pois' if '/place/' in key[2] else ('route' if '/direction/' in key[2] else None)
        if field and not isinstance(data.get(field), list if field == 'pois' else dict):
            return None
        if field is None and not any(isinstance(data.get(k), list) and data[k] for k in ('forecasts', 'lives')):
            return None
        observed = self.clock() if observed_at is None else observed_at
        ttl = min(TTLS[key[2]], 900) if field == 'pois' and not data['pois'] else TTLS[key[2]]
        expires = observed + ttl
        if expires <= self.clock() or observed > self.clock() + 60:
            return None
        rh = response_hash(data)
        try:
            with self.connect() as conn:
                conn.execute('INSERT INTO responses VALUES(?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET url=excluded.url,body=excluded.body,observed=excluded.observed,expires=excluded.expires,hash=excluded.hash,source=excluded.source WHERE excluded.observed>=responses.observed',
                             (key[0], key[1], json.dumps(data, ensure_ascii=False), observed, expires, rh, source))
        except (OSError, sqlite3.Error) as exc:
            log.warning('Amap local cache write unavailable: %s', type(exc).__name__)
            return None
        return {'policy': POLICY, 'request_hash': key[0], 'response_hash': rh, 'observed_at': observed, 'expires_at': expires, 'source': source, 'cache_hit': False}


def cached_json(url, fetch):
    cache = AmapCache()
    with sync_lock(url):
        hit = cache.get(url)
        if hit is not None:
            from app.core.amap_budget import record_cache_hit
            record_cache_hit(hit[1])
            return hit[0]
        data = fetch()
        cache.put(url, data)
        return data


async def cached_json_async(url, fetch):
    cache = AmapCache()
    async with async_lock(url):
        hit = await asyncio.to_thread(cache.get, url)
        if hit is not None:
            from app.core.amap_budget import record_cache_hit
            await asyncio.to_thread(record_cache_hit, hit[1])
            return hit[0]
        data = await fetch()
        await asyncio.to_thread(cache.put, url, data)
        return data
