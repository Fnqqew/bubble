"""Actualizaciones: Bubble se fija si hay una versión nueva en GitHub y, si querés, se actualiza solo.

- Instalado desde el ZIP: baja la versión nueva (el mismo ZIP de «Releases»), se cierra, reemplaza sus archivos y se
  vuelve a abrir (ver update_helper.py). Tu configuración, lo que aprendió de tu voz y los modelos no se tocan: viven
  en otra carpeta (%APPDATA% y %LOCALAPPDATA%).
- Instalado con git (desarrollo): `git pull`, solo si no hay cambios propios sin guardar.

Se revisa al abrir, como mucho una vez cada 12 horas. «Más tarde» no vuelve a preguntar por esa versión hasta el día
siguiente (mientras tanto queda el link «Actualizar» abajo de la ventana).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from . import __version__

log = logging.getLogger(__name__)
REPO = "Fnqqew/bubble"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases/latest"
PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
CHECK_EVERY_S = 12 * 3600
LATER_S = 24 * 3600
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
Progress = Callable[[str, float], None]  # (qué está haciendo, 0..1; -1 si no se sabe cuánto falta)


class UpdateError(Exception):
    """No se puede actualizar solo (el texto se muestra tal cual)."""


@dataclass
class Release:
    version: str
    notes: str = ""
    url: str = RELEASES_URL
    zip_url: str = ""


# ---------------------------------------------------------------- versiones
def parse_version(text: str) -> tuple[int, ...]:
    match = re.match(r"v?(\d+(?:\.\d+)*)", (text or "").strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else ()


def is_newer(latest: str, current: str = __version__) -> bool:
    new, old = parse_version(latest), parse_version(current)
    if not new:
        return False
    width = max(len(new), len(old))
    return new + (0,) * (width - len(new)) > old + (0,) * (width - len(old))


def install_kind(folder: Path = PROJECT_DIR) -> str:
    """"git" (una copia de desarrollo), "zip" (la carpeta del ZIP de «Releases») o "" (no se puede actualizar solo)."""
    if not (folder / "src" / "bubble" / "__init__.py").exists() or not (folder / "pyproject.toml").exists():
        return ""
    return "git" if (folder / ".git").exists() else "zip"


def plain_notes(markdown: str, limit: int = 900) -> str:
    """Las novedades en texto simple (sin imágenes, enlaces ni marcas de formato), para mostrar en la ventana."""
    text = re.sub(r"<[^>]+>", "", markdown or "")
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.lower().startswith("co-authored-by"):
            continue
        heading = line.startswith("#")
        line = re.sub(r"^#+\s*", "", line)
        line = re.sub(r"^>\s?", "", line)
        line = re.sub(r"^[-*]\s+", "• ", line)
        line = line.replace("**", "").replace("`", "")
        previous = lines[-1] if lines else ""
        # Los textos vienen cortados cada ~100 letras (mensajes de git, Markdown): se juntan de nuevo en párrafos.
        if line and previous and not heading and not line.startswith("• ") and (
                raw[:1].isspace() or not previous.startswith("• ")):
            lines[-1] = f"{previous} {line}"
        elif line or previous:
            lines.append(line)
    text = "\n".join(lines).strip()
    if len(text) > limit:
        text = text[:limit].rsplit("\n", 1)[0].rstrip() + "\n…"
    return text


# ---------------------------------------------------------------- ¿hay una versión nueva?
def _git(folder: Path, *args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}  # nunca te pide nada
    return subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, env=env, creationflags=NO_WINDOW)


def latest_from_github(timeout: float = 10) -> Release | None:
    """La última versión publicada (None si no hay, o si GitHub no la muestra: con el repositorio privado responde
    404 a quien no tiene cuenta)."""
    request = urllib.request.Request(API_LATEST, headers={"Accept": "application/vnd.github+json",
                                                          "User-Agent": f"Bubble/{__version__}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 404):
            return None
        raise
    tag = str(data.get("tag_name") or "")
    if not parse_version(tag):
        return None
    return Release(tag.lstrip("v"), str(data.get("body") or ""), str(data.get("html_url") or RELEASES_URL),
                   f"https://github.com/{REPO}/archive/refs/tags/{tag}.zip")


def latest_from_git(folder: Path = PROJECT_DIR) -> Release | None:
    """Con una copia de git: las versiones (etiquetas vX.Y.Z) del repositorio, con tu propia sesión de git."""
    if _git(folder, "fetch", "--tags", "--quiet", "origin").returncode != 0:
        return None
    tags = [tag for tag in _git(folder, "tag", "--list", "v*").stdout.split() if parse_version(tag)]
    if not tags:
        return None
    best = max(tags, key=parse_version)
    notes = _git(folder, "log", "-1", "--format=%B", best).stdout
    return Release(best.lstrip("v"), notes)


def latest(folder: Path = PROJECT_DIR) -> Release | None:
    release = None
    try:
        release = latest_from_github()
    except (OSError, ValueError):
        log.debug("No se pudo preguntar a GitHub", exc_info=True)
    if release is None and install_kind(folder) == "git" and shutil.which("git"):
        try:
            release = latest_from_git(folder)
        except (OSError, subprocess.SubprocessError):
            log.debug("No se pudo preguntar con git", exc_info=True)
    return release


def check(force: bool = False, current: str = __version__) -> Release | None:
    """La versión nueva, si hay (None si estás al día o no se pudo saber). Sin `force`, pregunta como mucho una vez
    cada 12 horas (si no, usa lo que se supo la última vez)."""
    from .state import load_state, update_state

    saved = load_state().get("update") or {}
    if not force and time.time() - float(saved.get("checked_at", 0)) < CHECK_EVERY_S:
        known = saved.get("latest")
        release = Release(**known) if isinstance(known, dict) else None
    else:
        release = latest()
        update_state(update={**saved, "checked_at": time.time(), "latest": asdict(release) if release else None})
    return release if release is not None and is_newer(release.version, current) else None


def should_offer(release: Release) -> bool:
    """¿Se pregunta sola? (no, si para esta versión tocaste «Más tarde» hace menos de un día)"""
    from .state import load_state

    later = (load_state().get("update") or {}).get("later") or {}
    return not (later.get("version") == release.version and time.time() - float(later.get("at", 0)) < LATER_S)


def remind_later(release: Release) -> None:
    from .state import load_state, update_state

    saved = load_state().get("update") or {}
    update_state(update={**saved, "later": {"version": release.version, "at": time.time()}})


# ---------------------------------------------------------------- preparar (con Bubble abierto)
def work_dir() -> Path:
    return Path(tempfile.gettempdir()) / "bubble-actualizacion"


def download(release: Release, folder: Path, progress: Progress, timeout: float = 30) -> Path:
    """Baja el ZIP de la versión y lo descomprime. Devuelve la carpeta de Bubble que venía adentro."""
    archive = folder / "bubble.zip"
    request = urllib.request.Request(release.zip_url, headers={"User-Agent": f"Bubble/{__version__}"})
    with urllib.request.urlopen(request, timeout=timeout) as response, archive.open("wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while chunk := response.read(256 * 1024):
            out.write(chunk)
            done += len(chunk)
            size = f"{done / 1e6:.1f}".replace(".", ",")
            progress(f"Bajando Bubble {release.version}… {size} MB", done / total if total else -1)
    progress("Descomprimiendo…", -1)
    target = folder / "nueva"
    try:
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(target)
    except zipfile.BadZipFile as exc:
        raise UpdateError("La descarga vino rota: probá de nuevo más tarde.") from exc
    roots = [path.parent.parent.parent for path in target.glob("*/src/bubble/__init__.py")]
    if len(roots) != 1 or not (roots[0] / "pyproject.toml").exists():
        raise UpdateError("La descarga no tiene a Bubble adentro: probá de nuevo más tarde.")
    return roots[0]


def prepare(release: Release, progress: Progress, folder: Path = PROJECT_DIR) -> Path:
    """Deja todo listo para actualizar (lo que se baja, se baja acá, con Bubble abierto) y devuelve la carpeta de
    trabajo con el plan para update_helper.py."""
    kind = install_kind(folder)
    if not kind:
        raise UpdateError("Esta copia de Bubble no se puede actualizar sola: bajá la versión nueva de GitHub.")
    work = work_dir()
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    executable = Path(sys.executable)
    python, pythonw = executable.with_name("python.exe"), executable.with_name("pythonw.exe")
    if not python.exists():
        python = executable
    if not pythonw.exists():
        pythonw = python
    from .state import state_path

    plan = {"kind": kind, "project": str(folder), "pid": os.getpid(), "version": release.version,
            "python": str(python), "relaunch": [str(pythonw), "-m", "bubble"],
            "result": str(state_path().with_name("actualizacion.json"))}
    if kind == "git":
        if not shutil.which("git"):
            raise UpdateError("Falta git para actualizar esta copia: instalalo o usá el ZIP de GitHub.")
        progress("Revisando tus cambios…", -1)
        if _git(folder, "status", "--porcelain", "--untracked-files=no").stdout.strip():
            raise UpdateError("Tenés cambios propios sin guardar en la carpeta de Bubble: guardalos (commit) o "
                              "descartalos y probá de nuevo.")
    else:
        if not release.zip_url:
            raise UpdateError("No encontré la descarga de esa versión: bajala de GitHub.")
        plan["staged"] = str(download(release, work, progress))
    shutil.copy(Path(__file__).with_name("update_helper.py"), work / "actualizar.py")
    (work / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    progress("Listo para actualizar.", 1.0)
    return work


def launch(work: Path) -> None:
    """Arranca el que termina la actualización (espera a que Bubble se cierre). Después hay que cerrar Bubble."""
    plan = json.loads((work / "plan.json").read_text(encoding="utf-8"))
    command = [plan["relaunch"][0], str(work / "actualizar.py"), str(work / "plan.json")]
    detached = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: sigue al cerrarse Bubble
    try:
        subprocess.Popen(command, cwd=str(work), close_fds=True, creationflags=detached | 0x01000000)  # +BREAKAWAY
    except OSError:
        subprocess.Popen(command, cwd=str(work), close_fds=True, creationflags=detached)


def finished() -> dict | None:
    """Cómo terminó la última actualización (una sola vez: se borra al leerla)."""
    from .state import state_path

    path = state_path().with_name("actualizacion.json")
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    path.unlink(missing_ok=True)
    shutil.rmtree(work_dir(), ignore_errors=True)  # lo que se bajó (ya no hace falta)
    return result
