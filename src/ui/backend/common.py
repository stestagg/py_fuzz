"""Shared helpers for the pyfuzz UI backend.

Importing this module puts the repo's src/ directory on sys.path so that
``pyfuzz.*`` imports work; import it before any ``pyfuzz`` import.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
PYTHON_SRC = REPO_ROOT / "src"
if str(PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(PYTHON_SRC))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_bytes(data: Any) -> bytes:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
