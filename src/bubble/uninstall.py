"""Desinstalar Bubble: borra todo lo que trajo (ver ui/setup_window.py: UninstallWindow).

- Tu configuración, tu clave de Pro, lo aprendido de tu voz y los registros (%APPDATA%\\Bubble y %LOCALAPPDATA%\\Bubble).
- Los modelos de voz y las voces descargadas (%LOCALAPPDATA%\\Bubble\\models).
- El acceso directo del escritorio.
- El micrófono virtual (VB-Cable), si querés: es un controlador de Windows y otros programas pueden usarlo, así que se
  abre su desinstalador oficial (pide permiso de administrador).
- La carpeta del programa. Si es una carpeta de desarrollo (con git), no se toca.

Antes de borrar, Windows vuelve a usar tu micrófono y tu parlante de verdad. Lo que Bubble tiene abierto mientras corre
(sus registros, el Python del programa) se borra apenas se cierra, con un paso que corre solo después.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)
PROJECT_DIR = Path(__file__).resolve().parent.parent.parent


@dataclass
class Part:
    key: str
    title: str
    detail: str
    paths: list[Path]
    selected: bool = True
    available: bool = True


def _roaming() -> Path:
    return Path(os.environ.get("APPDATA") or Path.home() / ".config") / "Bubble"


def _local() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache") / "Bubble"


def size_of(paths: list[Path]) -> int:
    total = 0
    for path in paths:
        if path.is_file():
            total += path.stat().st_size
        elif path.is_dir():
            for root, _dirs, files in os.walk(path):
                for name in files:
                    try:
                        total += os.path.getsize(os.path.join(root, name))
                    except OSError:
                        pass
    return total


def human(size: int) -> str:
    for unit, factor in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if size >= factor:
            return f"{size / factor:.1f} {unit}".replace(".", ",")
    return f"{size} B"


def _desktop_link() -> Path | None:
    try:
        from .shortcut import SHORTCUT_NAME, _desktop_dir

        return _desktop_dir() / SHORTCUT_NAME
    except Exception:  # noqa: BLE001
        return None


def is_development(folder: Path = PROJECT_DIR) -> bool:
    """Una carpeta de desarrollo (con git): el código no se borra nunca."""
    return (folder / ".git").exists()


def is_bubble_folder(folder: Path) -> bool:
    """¿Es de verdad la carpeta de Bubble? (si Bubble corre instalado en otro lado, no se borra nada que no sea suyo)"""
    return (folder / "pyproject.toml").is_file() and (folder / "src" / "bubble" / "__init__.py").is_file()


def parts(project: Path = PROJECT_DIR) -> list[Part]:
    local, roaming = _local(), _roaming()
    data = [roaming, *(p for p in local.glob("*") if p.name not in ("models", "descargas"))] if local.exists() else [
        roaming]
    models = [local / "models", local / "descargas"]
    link = _desktop_link()
    cable = False
    try:
        from .install import _cable_ready

        cable = _cable_ready()
    except Exception:  # noqa: BLE001
        pass
    development = is_development(project)
    removable = is_bubble_folder(project) and not development
    return [
        Part("datos", "Tu configuración y lo aprendido",
             "Ajustes, tu clave de Pro, tu perfil de voz, tus grabaciones y registros.", [p for p in data if p.exists()]),
        Part("modelos", "Modelos y voces descargados", f"{human(size_of(models))} en tu disco.",
             [p for p in models if p.exists()]),
        Part("accesos", "Acceso directo", "El de tu escritorio.", [link] if link and link.exists() else []),
        Part("cable", "Micrófono virtual (VB-Cable)", "El controlador que instaló Bubble: se saca de Windows como si "
             "nunca hubiera estado (Windows pide permiso de administrador).", [], selected=cable, available=cable),
        Part("programa", "La carpeta de Bubble",
             str(project) if removable else "Es una carpeta de desarrollo (git): no se borra." if development
             else "Bubble no está en una carpeta propia: no se borra.",
             [project] if removable else [], selected=removable, available=removable),
    ]


def run(selected: set[str], project: Path = PROJECT_DIR, after_exit: bool = True) -> list[str]:
    """Desinstala lo elegido. Devuelve lo que hizo (para mostrar). Lo que no se pudo borrar ahora (en uso) se borra
    cuando Bubble se cierra."""
    done: list[str] = []
    try:
        from .voice.devices import restore_real_defaults

        fixed = restore_real_defaults()
        if fixed:
            done.append(f"Windows volvió a usar: {', '.join(fixed)}")
    except Exception:  # noqa: BLE001 - sin la parte de voz no hay nada que devolver
        log.debug("No se pudieron devolver los dispositivos", exc_info=True)
    later: list[Path] = []
    ordered = parts(project)
    # El micrófono virtual primero: su desinstalador está en las descargas, que se borran después.
    ordered.sort(key=lambda part: part.key != "cable")
    for part in ordered:
        if part.key not in selected or not part.available:
            continue
        if part.key == "cable":
            done.append(_uninstall_cable())
            continue
        if part.key == "programa":
            later.extend(part.paths)  # el programa está corriendo: se borra al cerrarse
            done.append("La carpeta de Bubble se borra al cerrarse.")
            continue
        for path in part.paths:
            if path.is_file():
                try:
                    path.unlink()
                except OSError:
                    later.append(path)
            elif path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
                if path.exists():
                    later.append(path)  # (registros abiertos, modelos en uso)
        done.append(f"{part.title}: listo.")
    if later and after_exit:
        _delete_after_exit(later)
    return done


def _cable_setup() -> Path | None:
    """El instalador oficial de VB-Cable (sirve también para desinstalarlo), copiado a una carpeta temporal: las
    descargas de Bubble se borran enseguida. Si no está (se borró), se baja de nuevo."""
    import zipfile

    folder = _local() / "descargas" / "vbcable"
    if not (folder / "VBCABLE_Setup_x64.exe").exists():
        try:
            from .voice.bridge import _driver_url
            from .voice.models import download

            url = _driver_url()
            archive = download(url, Path(tempfile.gettempdir()) / "bubble-vbcable" / Path(url).name)
            folder = archive.parent
            with zipfile.ZipFile(archive) as pack:
                pack.extractall(folder)
        except Exception:  # noqa: BLE001 - sin internet: se avisa cómo hacerlo a mano
            log.debug("No se pudo bajar el instalador de VB-Cable", exc_info=True)
            return None
    copy = Path(tempfile.gettempdir()) / "bubble-vbcable-quitar"
    shutil.rmtree(copy, ignore_errors=True)
    shutil.copytree(folder, copy)
    setup = copy / "VBCABLE_Setup_x64.exe"
    return setup if setup.exists() else None


def _uninstall_cable() -> str:
    """Saca el controlador de VB-Cable con su instalador oficial, sin ventanas (-u -h). Windows pide permiso."""
    import ctypes

    setup = _cable_setup()
    if setup is None:
        return ("Micrófono virtual: desinstalalo desde Configuración de Windows › Aplicaciones "
                "(VB-Audio Virtual Cable).")
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", str(setup), "-u -h", str(setup.parent), 0)
    if result <= 32:
        return "Micrófono virtual: no se sacó (se canceló el permiso de administrador)."
    return "Micrófono virtual: sacado de Windows (si Windows lo pide, reiniciá la PC)."


def _delete_after_exit(paths: list[Path]) -> None:
    """Un paso que espera a que Bubble se cierre y borra lo que quedaba en uso (y después se borra a sí mismo)."""
    pid = os.getpid()
    lines = ["@echo off", "chcp 65001 >nul", ":espera",
             f'tasklist /FI "PID eq {pid}" | find "{pid}" >nul && (timeout /t 1 >nul & goto espera)']
    for path in paths:
        lines.append(f'if exist "{path}\\*" (rmdir /s /q "{path}") else (del /f /q "{path}" 2>nul)')
    lines.append('del "%~f0"')
    script = Path(tempfile.gettempdir()) / f"bubble-desinstalar-{pid}.cmd"
    script.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    # (desde la carpeta temporal: si no, no se podría borrar la carpeta desde la que corre)
    subprocess.Popen(["cmd", "/c", str(script)], cwd=tempfile.gettempdir(),
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                     | getattr(subprocess, "DETACHED_PROCESS", 0), close_fds=True)
