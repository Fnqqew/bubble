"""Termina una actualización de Bubble: espera a que Bubble se cierre, reemplaza sus archivos (o hace `git pull`),
reinstala los paquetes si cambiaron y lo vuelve a abrir, con una ventanita que muestra cómo va.

Corre desde una copia en la carpeta temporal (así puede reemplazar la de Bubble) y solo usa Python estándar: no
importa nada de Bubble. Lo prepara y lo arranca update.py.

Uso: actualizar.py <plan.json> [--sin-ventana]
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

KEEP = {".venv", ".git"}  # lo que una actualización nunca toca
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def wait_for(pid: int, timeout: float = 90.0) -> bool:
    """Espera a que ese proceso termine. True si terminó."""
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        return True  # ya no existe
    try:
        return kernel32.WaitForSingleObject(handle, int(timeout * 1000)) == 0
    finally:
        kernel32.CloseHandle(handle)


def dependencies(project: Path) -> str:
    """El pyproject.toml sin la línea de la versión: si cambia, cambiaron los paquetes que necesita Bubble."""
    try:
        text = (project / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return ""
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("version"))


def replace_files(project: Path, staged: Path) -> bool:
    """Pone la versión nueva en la carpeta de Bubble (sin tocar .venv ni .git). Si algo falla, deja la anterior
    como estaba. Devuelve si cambiaron los paquetes que necesita."""
    changed = dependencies(project) != dependencies(staged)
    package, backup = project / "src" / "bubble", project / "src" / "bubble.anterior"
    shutil.rmtree(backup, ignore_errors=True)
    package.rename(backup)
    try:
        shutil.copytree(staged / "src" / "bubble", package)
        for item in staged.iterdir():
            if item.name in KEEP or item.name == "src":
                continue
            if item.is_dir():
                shutil.copytree(item, project / item.name, dirs_exist_ok=True)
            else:
                shutil.copy2(item, project / item.name)
    except BaseException:
        shutil.rmtree(package, ignore_errors=True)
        backup.rename(package)
        raise
    shutil.rmtree(backup, ignore_errors=True)
    return changed


def git_pull(project: Path) -> bool:
    before = dependencies(project)
    done = subprocess.run(["git", "-C", str(project), "pull", "--ff-only", "--quiet"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
                          env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"})
    if done.returncode != 0:
        raise RuntimeError("git no pudo traer la versión nueva: " + (done.stderr.strip().splitlines() or ["?"])[-1])
    return dependencies(project) != before


def pip_install(python: str, project: Path) -> None:
    done = subprocess.run([python, "-m", "pip", "install", "--disable-pip-version-check", "-e", f"{project}[voz]"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    if done.returncode != 0:
        raise RuntimeError("no se pudieron instalar los paquetes nuevos (¿hay internet?)")


def run(plan: dict, say=print) -> dict:
    """Todos los pasos. Devuelve el resultado que lee Bubble al abrirse (ver update.finished)."""
    project = Path(plan["project"])
    result = {"version": plan["version"], "ok": False, "error": ""}
    try:
        say("Esperando que Bubble se cierre…")
        wait_for(int(plan["pid"]))
        time.sleep(0.5)  # (que Windows suelte los archivos)
        say(f"Poniendo Bubble {plan['version']}…")
        changed = git_pull(project) if plan["kind"] == "git" else replace_files(project, Path(plan["staged"]))
        if changed:
            say("Instalando lo nuevo que necesita (puede tardar un par de minutos)…")
            pip_install(plan["python"], project)
        result["ok"] = True
    except Exception as exc:  # noqa: BLE001 - se avisa y Bubble se abre igual (con la versión que quedó)
        result["error"] = str(exc) or type(exc).__name__
    try:
        Path(plan["result"]).parent.mkdir(parents=True, exist_ok=True)
        Path(plan["result"]).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    say("✓ ¡Listo! Abriendo Bubble…" if result["ok"] else f"No se pudo actualizar: {result['error']}")
    subprocess.Popen(plan["relaunch"], cwd=str(project), close_fds=True, creationflags=0x00000008)  # DETACHED
    return result


def build_window(root, version: str):
    """La ventanita «Actualizando Bubble» (oscura, como Bubble). Devuelve el texto de estado y la barra."""
    import tkinter as tk
    from tkinter import ttk

    background, text, muted, accent = "#1c1c1c", "#f3f3f3", "#a8adb6", "#57a9ff"
    root.title("Actualizando Bubble")
    root.configure(background=background)
    root.resizable(False, False)
    root.attributes("-topmost", True)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("Bubble.Horizontal.TProgressbar", troughcolor="#2d2d2d", background=accent, bordercolor=background,
                    lightcolor=accent, darkcolor=accent, thickness=6)
    frame = tk.Frame(root, background=background, padx=28, pady=24)
    frame.pack()
    tk.Label(frame, text=f"Actualizando Bubble a la {version}", font=("Segoe UI Semibold", 14),
             background=background, foreground=text).pack(anchor="w")
    tk.Label(frame, text="Tu configuración, tu voz y lo descargado no se tocan.", font=("Segoe UI", 9),
             background=background, foreground=muted).pack(anchor="w", pady=(2, 0))
    status = tk.Label(frame, text="", font=("Segoe UI", 10), background=background, foreground=text,
                      wraplength=380, justify="left")
    status.pack(anchor="w", pady=(16, 10))
    bar = ttk.Progressbar(frame, mode="indeterminate", length=380, style="Bubble.Horizontal.TProgressbar")
    bar.pack()
    try:  # la barra de título oscura (Windows 10 20H1+ / 11)
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        dark = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark))
    except Exception:  # noqa: BLE001
        pass
    return status, bar


def main() -> None:
    plan = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if sys.stdout is not None:
        sys.stdout.reconfigure(errors="replace")  # (una consola sin «✓» no lo frena)
    if "--sin-ventana" in sys.argv:
        run(plan)
        return
    import tkinter as tk

    root = tk.Tk()
    try:  # el ícono de Bubble (se carga antes de reemplazar los archivos)
        root.iconbitmap(default=str(Path(plan["project"]) / "src" / "bubble" / "assets" / "bubble.ico"))
    except tk.TclError:
        pass
    status, bar = build_window(root, plan["version"])
    bar.start(12)
    root.update_idletasks()
    width, height = root.winfo_reqwidth(), root.winfo_reqheight()
    root.geometry(f"+{(root.winfo_screenwidth() - width) // 2}+{(root.winfo_screenheight() - height) // 3}")
    messages: list[str] = []
    finished: list[dict] = []

    def work() -> None:
        finished.append(run(plan, messages.append))

    def poll() -> None:
        if messages:
            status.configure(text=messages[-1])
        if finished:
            bar.stop()
            root.after(1200 if finished[0]["ok"] else 6000, root.destroy)
            return
        root.after(100, poll)

    threading.Thread(target=work, daemon=True).start()
    poll()
    root.mainloop()


if __name__ == "__main__":
    main()
