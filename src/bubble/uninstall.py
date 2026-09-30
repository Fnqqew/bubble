"""Desinstala Bubble y borra todo lo que instaló (ver ui/setup_window.py: UninstallWindow).

- La configuración, la clave de Pro, lo aprendido de la voz del jugador y los registros (%APPDATA%\\Bubble y
  %LOCALAPPDATA%\\Bubble).
- Los modelos de voz y las voces descargadas (%LOCALAPPDATA%\\Bubble\\models).
- El acceso directo del escritorio.
- Opcionalmente, el micrófono virtual (VB-Cable): es un controlador de Windows que otros programas pueden usar, por lo
  que se abre su desinstalador oficial (requiere permisos de administrador).
- La carpeta del programa. Si es una carpeta de desarrollo (con git), no se toca.

Antes de borrar, Windows vuelve a usar el micrófono y el parlante reales. Lo que Bubble mantiene abierto mientras corre
(sus registros, el Python del programa) se borra al cerrarse, mediante un paso posterior que se ejecuta de forma
independiente.
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
    """Indica si es una carpeta de desarrollo (con git); su código nunca se borra."""
    return (folder / ".git").exists()


def is_bubble_folder(folder: Path) -> bool:
    """Indica si la carpeta es realmente la de Bubble. Si Bubble corre instalado en otra ubicación, no se borra nada
    que no le pertenezca.
    """
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
        Part("cable", "Micrófono virtual (VB-Cable)", "El micrófono que instaló Bubble. Se quita de Windows por "
                                                      "completo (te va a pedir permiso).", [], selected=cable,
             available=cable),
        Part("programa", "La carpeta de Bubble",
             str(project) if removable else "Es una carpeta de desarrollo (git): no se borra." if development
             else "Bubble no está en una carpeta propia: no se borra.",
             [project] if removable else [], selected=removable, available=removable),
    ]


def run(selected: set[str], project: Path = PROJECT_DIR, after_exit: bool = True) -> list[str]:
    """Desinstala los componentes elegidos y devuelve la lista de acciones realizadas (para mostrarlas). Lo que no se
    puede borrar en el momento (por estar en uso) se elimina cuando Bubble se cierra.
    """
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
    # El micrófono virtual va primero: su desinstalador está en las descargas, que se borran después.
    ordered.sort(key=lambda part: part.key != "cable")
    for part in ordered:
        if part.key not in selected or not part.available:
            continue
        if part.key == "cable":
            done.append(_uninstall_cable())
            continue
        if part.key == "programa":
            later.extend(part.paths)  # el programa está en ejecución: se borra al cerrarse
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
    """Devuelve el instalador oficial de VB-Cable (también sirve para desinstalarlo), copiado a una carpeta temporal
    porque las descargas de Bubble se borran enseguida. Si ya no está, se descarga de nuevo.
    """
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
    """Quita el controlador de VB-Cable con su instalador oficial, sin ventanas (-u -h). Windows solicita permisos."""
    import ctypes

    setup = _cable_setup()
    if setup is None:
        return ("El micrófono virtual lo desinstalás desde Windows, en Aplicaciones (se llama VB-Audio Virtual Cable).")
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", str(setup), "-u -h", str(setup.parent), 0)
    if result <= 32:
        return "El micrófono virtual quedó instalado porque se canceló el permiso."
    return "Micrófono virtual quitado. Si Windows lo pide, reiniciá la PC."


def _delete_after_exit(paths: list[Path]) -> None:
    """Paso independiente que espera a que Bubble se cierre, borra lo que quedó en uso y luego se elimina a sí mismo."""
    pid = os.getpid()
    lines = ["@echo off", "chcp 65001 >nul", ":espera",
             f'tasklist /FI "PID eq {pid}" | find "{pid}" >nul && (timeout /t 1 >nul & goto espera)']
    for path in paths:
        lines.append(f'if exist "{path}\\*" (rmdir /s /q "{path}") else (del /f /q "{path}" 2>nul)')
    lines.append('del "%~f0"')
    script = Path(tempfile.gettempdir()) / f"bubble-desinstalar-{pid}.cmd"
    script.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    # (desde la carpeta temporal: de lo contrario no se podría borrar la carpeta desde la que corre)
    subprocess.Popen(["cmd", "/c", str(script)], cwd=tempfile.gettempdir(),
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                     | getattr(subprocess, "DETACHED_PROCESS", 0), close_fds=True)
