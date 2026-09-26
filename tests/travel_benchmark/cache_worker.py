"""Versioned cache-only overlay: execute an unchanged, hash-checked frozen worker.

Invoked by absolute path from a frozen runtime cwd. This module and amap_cache.py
are copied together into the experiment policy directory before dispatch.
"""
from __future__ import annotations
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import time


def main():
    policy_dir = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location('benchmark_amap_cache', policy_dir/'amap_cache.py')
    cache_module = importlib.util.module_from_spec(spec); spec.loader.exec_module(cache_module)
    sys.path.insert(0, str(Path.cwd()))
    import httpx
    from tests.travel_benchmark import runner
    from tests.travel_benchmark.common import clean, digest, read
    from app.core.http import redact_url
    manifest = read(policy_dir/'policy.json')
    import hashlib
    def check():
        for name, expected in manifest['code_hashes'].items():
            if hashlib.sha256((policy_dir/name).read_bytes()).hexdigest() != expected:
                raise RuntimeError('Cache overlay code drift: '+name)
    check()
    original_request = httpx.AsyncClient.request
    original_save = runner.Capture.save
    events = []
    cache = cache_module.AmapCache()
    # Newer production runtimes also cache HTTP; let the overlay own this cache
    # so hits remain observable. Existing frozen overlay files are unchanged.
    cache.enabled = True
    os.environ['AMAP_CACHE_ENABLED'] = '0'

    async def request(client, method, url, *args, **kwargs):
        full = str(httpx.Request(method, url, params=kwargs.get('params')).url) if kwargs.get('params') else str(url)
        if method.upper() != 'GET' or cache_module.identity(full) is None:
            return await original_request(client, method, url, *args, **kwargs)
        async with cache_module.async_lock(full):
            hit = await asyncio.to_thread(cache.get, full)
            if hit is not None:
                body, metadata = hit
                response = httpx.Response(200, json=body, request=httpx.Request(method, full))
            else:
                if os.getenv('BENCHMARK_BUDGET_DB'):
                    from tests.travel_benchmark.budget import reserve
                    reserve(os.environ['BENCHMARK_BUDGET_DB'],int(os.environ['BENCHMARK_NETWORK_LIMIT']),
                            os.environ.get('BENCHMARK_TRIAL',''),redact_url(full))
                response = await original_request(client, method, url, *args, **kwargs)
                try: body = response.json()
                except ValueError: body = None
                metadata = None
                if response.status_code == 200:
                    metadata = await asyncio.to_thread(cache.put, full, body, source='experiment-live')
                metadata = metadata or {'cache_hit':False, 'policy':cache_module.POLICY, 'stored':False}
            events.append({'url':redact_url(full), **metadata})
            return response

    def save(self):
        self.record['cache_policy'] = manifest['id']
        self.record['cache_overlay_hash'] = digest(manifest)
        self.record['amap_request_cache'] = clean(events)
        # The frozen capture historically called every returned response api_calls.
        # Preserve that list and explicitly distinguish local hits from network calls.
        for call, event in zip(self.record.get('api_calls', []), events):
            if call['url'] != event['url']:
                raise RuntimeError('Cache request evidence order mismatch')
            call['cache_hit'] = event['cache_hit']
        original_save(self)

    httpx.AsyncClient.request = request
    runner.Capture.save = save
    try:
        from tests.travel_benchmark.__main__ import main as worker_main
        worker_main()
    finally:
        check()


if __name__ == '__main__': main()
