"""Load `<project>/.env` into os.environ (existing variables win). No dependency on python-dotenv.

The CRM server is started by the engine's supervisor task, so there is no shell profile to export secrets
from; the facade token and the multi-user switch live in .env instead (ignored by git).

Recognised keys:
  STOCKCRM_FACADE_TOKEN   shared secret the facade (and the engine's own calls) send as X-Facade-Token
  STOCKCRM_MULTIUSER      1 = API requests without a valid identity get 401
  STOCKCRM_USER_ID        which user the MCP server and scripts act as (default 1)
  STOCKCRM_DB             database path override
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def parse_env(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:]
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()
        if key:
            out[key] = value
    return out


def load_dotenv(path: Path | None = None, override: bool = False) -> dict[str, str]:
    p = path or ROOT / ".env"
    if not p.exists():
        return {}
    values = parse_env(p.read_text(encoding="utf-8"))
    for k, v in values.items():
        if override or k not in os.environ:
            os.environ[k] = v
    return values
