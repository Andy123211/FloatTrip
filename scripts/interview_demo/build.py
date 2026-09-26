"""Build once from frozen C5; never modifies benchmark or current C6 planning code."""
from pathlib import Path
import hashlib
import json
import shutil
import sqlite3
import time

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'artifacts/interview-demo/shanghai-c5-match-v1'
BASE = ROOT / 'artifacts/travel-benchmark/c5-online-v1/runtimes/C5'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replace(path, old, new):
    text = path.read_text()
    assert text.count(old) == 1, (path, old[:90], text.count(old))
    path.write_text(text.replace(old, new))


def build():
    if (OUT / 'manifest.json').exists():
        raise SystemExit('Already frozen. Use run.py; do not overwrite this delivery.')
    OUT.mkdir(parents=True, exist_ok=True)
    for folder in ['app', 'frontend']:
        source = BASE/folder if folder == 'app' else ROOT/folder
        shutil.copytree(source, OUT/folder, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store'))
    # Only the entity matching change is taken from C6; its calendar is NOT copied.
    for name in ['planning/grounded.py', 'core/http.py', 'core/amap_cache.py', 'core/amap_budget.py']:
        shutil.copy2(ROOT/'app'/name, OUT/'app'/name)
    replace(OUT/'app/planning/grounded.py',
            "    if not options:return None,{'query':name,'status':'unresolved','returned_names':[r.get('name') for r in rows]}",
            "    # A provider's generic address label is not a second tourist destination.\n"
            "    # Keep two actual same-name destinations ambiguous, without merging IDs.\n"
            "    if options:\n"
            "        top = options[0][:2]\n"
            "        typed = [x for x in options if x[:2] == top and str(x[3].get('typecode') or '').startswith(('11', '14', '08', '12'))]\n"
            "        if typed:\n"
            "            options = [x for x in options if not (x[:2] == top and str(x[3].get('typecode') or '').startswith('19'))]\n"
            "    if not options:return None,{'query':name,'status':'unresolved','returned_names':[r.get('name') for r in rows]}")
    assert sha(OUT/'app/planning/reliability.py') == sha(BASE/'app/planning/reliability.py')
    replace(OUT/'app/planning/graph.py', '_NODE_LABELS: dict[str, str] = {',
            '_NODE_LABELS: dict[str, str] = {\n    "requirements_and_draft": "正在结合你的要求挑选景点",')
    for old, new in [('🗺 正在调用高德搜索景点池','正在核实景点位置与信息'),
                     ('🧩 正在生成候选景点旅游语义','正在比较适合你的景点'),
                     ('🧮 正在确定性选择并优化景点路线','正在安排每天的路线与时间'),
                     ('✅ 正在独立复算与校验路线','正在检查必去、时间与交通要求')]:
        replace(OUT/'app/planning/graph.py', old, new)
    replace(OUT/'app/planning/runtime_worker.py',
            '        itinerary_id = await asyncio.to_thread(self._persist, run, state)',
            '        if (state.final_plan.get("validation_summary") or {}).get("status") == "failed":\n'
            '            raise RuntimeError("行程未通过要求检查，未保存为成功结果。")\n'
            '        itinerary_id = await asyncio.to_thread(self._persist, run, state)')
    replace(OUT/'app/main.py', '# ─── 静态文件（前端）',
            '@app.get("/api/demo/status")\ndef demo_status():\n'
            '    from app.core.amap_budget import status\n'
            '    return {"version": "C5+entity-match-v1", "budget": status()}\n\n'
            '# ─── 静态文件（前端）')
    api = OUT/'frontend/api.js'
    replace(api, 'plugins: ["AMap.Driving", "AMap.Walking", "AMap.InfoWindow"],',
            'plugins: ["AMap.InfoWindow"],')
    # Remove direct browser routing completely; all backend requests remain budgeted.
    text = api.read_text()
    start = text.index('  if (!inst.driving) {', text.index('function drawRealRoute'))
    end = text.index('\n}\n', start)
    text = text[:start] + '  fallbackLine();\n  inst.map.setFitView(null, false, [48, 48, 48, 48]);' + text[end:]
    api.write_text(text)
    replace(api, '    logs: backendPlan.history || [],',
            '    validation_summary: backendPlan.validation_summary || null,\n'
            '    pending_checks: backendPlan.pending_checks || [],\n'
            '    logs: backendPlan.history || [],')
    # The top demo banner was removed at the user's request. Backend validation
    # and the durable API budget still apply independently of presentation.
    # Seed valid public cache entries only, preserving collection times and hashes.
    cache_dest = OUT/'data/amap-cache.sqlite'
    cache_dest.parent.mkdir(exist_ok=True)
    dest = sqlite3.connect(cache_dest)
    dest.execute('CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY,url TEXT NOT NULL,body TEXT NOT NULL,observed REAL NOT NULL,expires REAL NOT NULL,hash TEXT NOT NULL,source TEXT NOT NULL)')
    seeds = []
    paths = [ROOT/'data/amap-cache.sqlite', ROOT/'artifacts/travel-benchmark/c5-online-v1/cache.sqlite']
    paths += [ROOT/'artifacts/travel-benchmark/c5-online-v1/cache_policy_v1/responses.sqlite']
    for path in sorted(set(paths)):
        if not path.exists(): continue
        with sqlite3.connect(f'file:{path}?mode=ro',uri=True) as src:
            if not src.execute("SELECT name FROM sqlite_master WHERE name='responses'").fetchone(): continue
            rows = src.execute('SELECT * FROM responses WHERE expires>?',(time.time(),)).fetchall()
        for row in rows:
            dest.execute('INSERT INTO responses VALUES(?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET url=excluded.url,body=excluded.body,observed=excluded.observed,expires=excluded.expires,hash=excluded.hash,source=excluded.source WHERE excluded.observed>responses.observed',row)
        seeds.append({'path':str(path.relative_to(ROOT)), 'eligible_rows':len(rows)})
    count = dest.execute('SELECT count(*) FROM responses').fetchone()[0]
    dest.commit();dest.close()
    manifest = {'version':'C5+entity-match-v1', 'created_at':time.time(), 'base':str(BASE.relative_to(ROOT)),
                'calendar_sha256':sha(OUT/'app/planning/reliability.py'), 'cache_seeds':seeds,'seeded_entries':count,
                'network_authorized':None,'live_network_budget':{'unlimited':True,'authorized_by_user':'本地演示上限50次解除'},'rehearsal_limit':30,'original_default':'A',
                'files':{str(p.relative_to(OUT)):sha(p) for folder in ['app','frontend'] for p in sorted((OUT/folder).rglob('*')) if p.is_file()},
                'dependency_hash':sha(ROOT/'requirements.txt')}
    (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'directory':str(OUT),'files':len(manifest['files']),'seeded_entries':count},ensure_ascii=False))


if __name__ == '__main__': build()
