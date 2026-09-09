"""Shared helpers for tools. Keep deterministic and dependency-light."""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import warnings
from urllib.parse import urlparse

# macOS system Python ships LibreSSL; urllib3 v2 warns on every import. Harmless here.
warnings.filterwarnings("ignore", message=r"urllib3 v2 only supports OpenSSL.*")

ROOT = pathlib.Path(__file__).resolve().parent.parent
TMP = ROOT / ".tmp"
DELIVERABLES = ROOT / "deliverables"


def load_env() -> None:
    """Load KEY=VALUE lines from .env into os.environ (no external deps).

    Uses setdefault, so a real environment variable always wins over .env.
    That is what lets the same code run unchanged in a cloud sandbox where
    secrets arrive as injected env vars and there is no .env file at all.
    """
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def require_env(name: str) -> str:
    load_env()
    value = os.environ.get(name)
    if not value:
        sys.exit(f"Missing required env var: {name} (set it in .env)")
    return value


def optional_env(name: str, default: str = "") -> str:
    load_env()
    return os.environ.get(name) or default


def write_tmp(name: str, data) -> pathlib.Path:
    path = TMP / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, (dict, list)):
        path.write_text(json.dumps(data, indent=2))
    else:
        path.write_text(str(data))
    return path


def read_tmp(name: str):
    """Read a file under .tmp/ ; JSON-decode if it looks like JSON."""
    path = TMP / name
    text = path.read_text()
    if name.endswith(".json"):
        return json.loads(text)
    return text


def slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s or "item"


def domain_of(url: str) -> str:
    """Bare registrable-ish host: strip scheme, 'www.', path, port."""
    if "//" not in url:
        url = "https://" + url
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def today() -> str:
    """The run date, YYYY-MM-DD. Single source of truth for stage filenames."""
    import datetime

    return datetime.date.today().isoformat()


def stage_path(stage: str, date: str | None = None) -> str:
    """Relative .tmp path for a pipeline stage's output, e.g. 'market/2026-09-09.json'."""
    return f"{stage}/{date or today()}.json"
