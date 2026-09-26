"""Import saved successful Amap HTTP responses without extending their original TTL.

Run from repository root: python -m scripts.seed_amap_cache --records DIR
Records contain started_at and api_calls[{url,status_code,response,cache_hit}].
No benchmark expectations, prompts, model suggestions or itinerary data are imported.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
from app.core.amap_cache import AmapCache


def seed(records, cache):
    counts=Counter()
    for path in records:
        record=json.loads(path.read_text())
        if not record.get('started_at'):continue
        observed=datetime.fromisoformat(record['started_at']).timestamp()
        events=record.get('amap_request_cache',[])
        for index,call in enumerate(record.get('api_calls',[])):
            counts['observed_records']+=1
            if call.get('status_code')!=200:continue
            stamp=observed
            if call.get('cache_hit'):
                event=events[index] if index<len(events) else {}
                if event.get('observed_at') is None:
                    counts['missing_original_timestamp']+=1;continue
                stamp=event['observed_at']
            if cache.put(call.get('url',''),call.get('response'),observed_at=stamp,source=str(path)):
                counts['eligible_success_records']+=1
    with cache.connect() as db:
        counts['live_unique_requests']=db.execute('SELECT count(*) FROM responses WHERE expires>?',(cache.clock(),)).fetchone()[0]
    return dict(counts)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--records',type=Path,required=True);parser.add_argument('--cache',type=Path)
    args=parser.parse_args();cache=AmapCache(args.cache)
    print(json.dumps({'cache':str(cache.path),**seed(sorted(args.records.glob('*/trials/*.json')),cache)},ensure_ascii=False))


if __name__=='__main__':main()
