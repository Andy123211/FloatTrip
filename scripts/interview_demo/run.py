"""Start the frozen local demo. Default phase is live; no budget is reset."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import sys
import importlib.metadata
import time

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'artifacts/interview-demo/shanghai-c5-match-v1'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=['rehearsal','live'], default='live')
    args = parser.parse_args()
    manifest = json.loads((OUT/'manifest.json').read_text())
    for name, digest in manifest['files'].items():
        if hashlib.sha256((OUT/name).read_bytes()).hexdigest() != digest:
            raise SystemExit('Frozen demo changed: '+name)
    if hashlib.sha256((ROOT/'requirements.txt').read_bytes()).hexdigest() != manifest['dependency_hash']:
        raise SystemExit('Dependency manifest changed; restore the validated environment before demo.')
    # Read secrets into this process only, before importing the isolated app.
    for line in (ROOT/'.env.local').read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k,v=line.split('=',1);os.environ.setdefault(k.strip(),v.strip().strip('"\''))
    public_config = {k: os.getenv(k) for k in ['LLM_PROVIDER', 'DEEPSEEK_MODEL',
        'MAIN_AGENT_MODEL', 'PLANNING_AGENT_MODEL', 'PLANNING_CANDIDATE_MODEL', 'CHAT_AGENT_MODE']}
    packages = sorted(f'{d.metadata["Name"]}=={d.version}' for d in importlib.metadata.distributions())
    frozen_config = {'model_config':public_config, 'installed_packages':packages, 'python':sys.version}
    config_path = OUT/'runtime_config.json'
    if config_path.exists() and json.loads(config_path.read_text()) != frozen_config:
        raise SystemExit('Model or dependency configuration drifted; restore the validated demo environment.')
    if not config_path.exists():
        config_path.write_text(json.dumps(frozen_config, ensure_ascii=False, indent=2)+'\n')
    os.environ.update(PLANNING_VARIANT='C', DEMO_PHASE=args.phase,
        RUNTIME_CHECKPOINT_DB=str(OUT/'data/checkpoints.db'),
        AMAP_CACHE_PATH=str(OUT/'data/amap-cache.sqlite'), AMAP_CACHE_ENABLED='1',
        AMAP_BUDGET_DB=str(OUT/'data/budget.sqlite'), AMAP_BUDGET_LIMIT='30',
        AMAP_BUDGET_UNLIMITED='1' if args.phase=='live' else '0')
    for key in list(os.environ):
        if key.startswith('BENCHMARK_'): os.environ.pop(key)
    sys.path.insert(0,str(OUT))
    os.chdir(OUT)
    from app.core.database import configure_database
    configure_database(OUT/'data/app.db')
    from app.core.amap_budget import status
    with (OUT/'startup_history.jsonl').open('a') as f:
        f.write(json.dumps({'at':time.time(),'phase':args.phase,'budget':status(),
            'manifest_sha256':hashlib.sha256((OUT/'manifest.json').read_bytes()).hexdigest()})+'\n')
    import uvicorn
    print('上海演示版 C5+地点匹配修复 · http://localhost:8766 · '+args.phase, flush=True)
    uvicorn.run('app.main:app',host='127.0.0.1',port=8766,log_level='info')


if __name__ == '__main__': main()
