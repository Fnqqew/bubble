"""Estado persistente de Bubble (%APPDATA%\\Bubble\\state.json): calibración del chat, tutorial visto, etc."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def state_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "Bubble" / "state.json"


def load_state() -> dict[str, Any]:
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def update_state(**values: Any) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    state = load_state()
    state.update(values)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
