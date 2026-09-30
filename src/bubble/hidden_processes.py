"""Evita que los procesos de Claude Code abran ventanas de consola.

Bubble se ejecuta sin consola (pythonw), por lo que Windows crea una ventana negra por cada claude.exe que lanza el
Agent SDK. El SDK no expone opciones de creación de procesos, pero usa `anyio.open_process`, que acepta `creationflags`:
se le añade CREATE_NO_WINDOW por defecto.
"""

from __future__ import annotations

import subprocess
import sys

_installed = False


def install() -> None:
    global _installed
    if _installed or sys.platform != "win32":
        return
    import anyio

    original = anyio.open_process

    async def open_process_hidden(*args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
        return await original(*args, **kwargs)

    anyio.open_process = open_process_hidden
    _installed = True
