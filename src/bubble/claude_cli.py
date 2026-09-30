"""Localiza el claude.exe con el que el jugador inició sesión en su suscripción."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path


class ClaudeNotFoundError(RuntimeError):
    pass


def candidate_paths() -> list[Path]:
    home = Path.home()
    candidates: list[Path] = []
    if found := shutil.which("claude.exe") or shutil.which("claude"):
        candidates.append(Path(found))
    # Instalador nativo (irm https://claude.ai/install.ps1 | iex).
    candidates.append(home / ".local" / "bin" / "claude.exe")
    # Binario incluido en la extensión de Claude Code para VS Code y Cursor.
    for editor_dir in (".vscode", ".vscode-insiders", ".cursor"):
        extensions = home / editor_dir / "extensions"
        if extensions.is_dir():
            candidates.extend(extensions.glob("anthropic.claude-code-*/resources/native-binary/claude.exe"))
    return candidates


def cli_version(path: Path) -> tuple[int, ...]:
    """Versión informada por `claude -v` (vacía si no responde)."""
    try:
        out = subprocess.run(
            [str(path), "-v"], capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ()
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
    return tuple(int(p) for p in match.groups()) if match else ()


def find_claude_cli(configured: str = "") -> str:
    if configured:
        if Path(configured).is_file():
            return configured
        raise ClaudeNotFoundError(f"No existe claude.exe en la ruta configurada: {configured}")
    # En Windows el SDK solo acepta un .exe nativo, no los shims .cmd de npm.
    usable = list(dict.fromkeys(
        p for p in candidate_paths() if p.is_file() and (os.name != "nt" or p.suffix.lower() == ".exe")
    ))
    if usable:
        # Puede haber varias instalaciones: se usa la más reciente, porque las antiguas no soportan todas las opciones.
        return str(max(usable, key=cli_version))
    raise ClaudeNotFoundError(
        "No se encontró Claude Code. Instalalo con:  irm https://claude.ai/install.ps1 | iex\n"
        "y ejecutá 'claude' una vez para iniciar sesión con tu suscripción."
    )
