from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(__file__).with_name("data")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def clean(value):
    text = json.dumps(value, ensure_ascii=False, default=str)
    # Redact credentials even when third-party exceptions include URLs/headers.
    for key, secret in os.environ.items():
        if any(token in key.upper() for token in ("API_KEY", "TOKEN", "PASSWORD", "SECRET")) and len(secret) >= 6:
            text = text.replace(secret, "<redacted>")
    text = re.sub(r"(?i)([?&](?:key|api_key|token|access_token)=)[^&\s\"\\]+", r"\1<redacted>", text)
    return json.loads(text)


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(clean(value), ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def now():
    return datetime.now(timezone.utc).isoformat()


def source_changes(manifest):
    """Check the frozen files again inside each worker, including after execution."""
    changed = []
    for name, expected in manifest['source_hashes'].items():
        path = ROOT / name
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        if actual != expected:
            changed.append({'path': name, 'expected': expected, 'actual': actual})
    return changed


def provenance():
    from app.core.env import load_local_env
    from app.llm.deepseek import resolve_deepseek_model
    load_local_env()
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()
    paths = sorted(set(ROOT.glob("app/**/*.py")) | set(ROOT.glob("app/planning/scoring_profiles/*.yaml"))
                   | set(DATA.glob("*.json")) | set(Path(__file__).parent.glob("*.py"))
                   | {Path(__file__).with_name("RUBRIC.md")})
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.exists()}
    model_vars = ("LLM_PROVIDER", "DEEPSEEK_MODEL", "PLANNING_AGENT_MODEL", "PLANNING_CANDIDATE_MODEL", "MAIN_AGENT_MODEL", "PLANNING_MEMORY_MATCH_MODEL")
    return {"created_at": now(), "git_head": git("rev-parse", "HEAD"), "git_status": git("status", "--short"),
            "source_hashes": hashes, "source_fingerprint": digest(hashes),
            "models": {k: os.getenv(k) for k in model_vars},
            "resolved_candidate_model": resolve_deepseek_model(os.getenv("PLANNING_CANDIDATE_MODEL") or os.getenv("PLANNING_AGENT_MODEL")),
            "resolved_main_agent_model": resolve_deepseek_model(os.getenv("MAIN_AGENT_MODEL")),
            "redis_configured": bool(os.getenv("REDIS_URL")), "timeout_seconds": 600}
