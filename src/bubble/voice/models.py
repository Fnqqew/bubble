"""Descarga (una sola vez) de los modelos de voz a %LOCALAPPDATA%\\Bubble\\models."""

from __future__ import annotations

import os
import urllib.request
from pathlib import Path
from typing import Callable

Progress = Callable[[str, float], None]  # (qué se descarga, 0..1)


def models_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    path = Path(base) / "Bubble" / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def download(url: str, target: Path, label: str = "", progress: Progress | None = None) -> Path:
    """Baja `url` a `target` si todavía no está (a un archivo temporal primero: una descarga cortada no queda a medias)."""
    if target.exists() and target.stat().st_size > 0:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "Bubble"})
    with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while chunk := response.read(1 << 16):
            out.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(label or target.name, done / total)
    partial.replace(target)
    return target
