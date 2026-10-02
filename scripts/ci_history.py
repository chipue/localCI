"""Small helpers for traceable localCI execution history."""
from __future__ import annotations

import datetime
import os
import uuid
from typing import Any


def execution_id(kind: str) -> str:
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{kind}-{stamp}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def event(name: str, **details: Any) -> dict[str, Any]:
    return {
        "event": name,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        **details,
    }
