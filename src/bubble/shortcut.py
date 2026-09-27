"""Acceso directo de Bubble en el escritorio, siempre sincronizado con el ícono y la instalación actual.

Windows guarda en caché los íconos por ruta de archivo: si el .ico cambia pero la ruta no, el escritorio
sigue mostrando el viejo. Por eso cada versión del ícono se copia con un nombre que incluye su hash.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent.parent
ICON_SOURCE = PACKAGE_DIR / "assets" / "bubble.ico"
SHORTCUT_NAME = "Bubble.lnk"


def _data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    return Path(base) / "Bubble"


def versioned_icon() -> Path:
    """Copia del ícono con su hash en el nombre (una ruta nueva por cada versión del ícono)."""
    digest = hashlib.sha1(ICON_SOURCE.read_bytes()).hexdigest()[:10]
    target = _data_dir() / "icons" / f"bubble-{digest}.ico"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        for old in target.parent.glob("bubble-*.ico"):
            old.unlink(missing_ok=True)
        shutil.copyfile(ICON_SOURCE, target)
    return target


def _pythonw() -> Path:
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return candidate if candidate.exists() else exe


def _desktop_dir() -> Path:
    buffer = ctypes.create_unicode_buffer(260)
    ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buffer)  # CSIDL_DESKTOPDIRECTORY
    return Path(buffer.value)


def _spec() -> dict:
    return {
        "target": str(_pythonw()),
        "args": "-m bubble",
        "workdir": str(PROJECT_DIR),
        "icon": str(versioned_icon()),
    }


def _state_file() -> Path:
    return _data_dir() / "shortcut.json"


def ensure_desktop_shortcut(force: bool = False) -> bool:
    """Crea o actualiza el acceso directo si algo cambió. Devuelve True si lo escribió."""
    spec = _spec()
    link = _desktop_dir() / SHORTCUT_NAME
    try:
        saved = json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    if not force and saved == spec and link.exists():
        return False

    def ps(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(" + ps(str(link)) + ");"
        f"$s.TargetPath = {ps(spec['target'])}; $s.Arguments = {ps(spec['args'])};"
        f"$s.WorkingDirectory = {ps(spec['workdir'])}; $s.IconLocation = {ps(spec['icon'] + ',0')};"
        "$s.Description = 'Bubble - traductor en tiempo real para Roblox'; $s.Save()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        check=True, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    _state_file().parent.mkdir(parents=True, exist_ok=True)
    _state_file().write_text(json.dumps(spec, indent=2), encoding="utf-8")
    # Avisa al Explorador que refresque los íconos.
    ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # SHCNE_ASSOCCHANGED
    return True


if __name__ == "__main__":
    print("Acceso directo actualizado." if ensure_desktop_shortcut(force=True) else "Sin cambios.")
