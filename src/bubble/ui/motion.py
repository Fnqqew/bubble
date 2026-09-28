"""Animaciones cortas y suaves: la ventana y la barra para escribir aparecen deslizándose, y se van desvaneciendo.
Todo corre en el hilo de la ventana (con `after`), de a ~60 cuadros por segundo, y dura menos de un cuarto de segundo:
se nota, pero nunca hace esperar."""

from __future__ import annotations

import time
import tkinter as tk
from typing import Callable

FRAME_MS = 15


def ease_out(t: float) -> float:
    """Arranca rápido y frena suave al llegar."""
    t = min(1.0, max(0.0, t))
    return 1 - (1 - t) ** 3


def animate(widget: tk.Misc, seconds: float, step: Callable[[float], None],
            done: Callable[[], None] | None = None, alive: Callable[[], bool] | None = None) -> None:
    """Llama a `step(avance 0→1, ya suavizado)` en cada cuadro durante `seconds`, y al final `done`. `alive`: si deja
    de ser cierto (otra animación la reemplazó), se corta sin tocar nada."""
    start = time.perf_counter()

    def frame() -> None:
        if alive is not None and not alive():
            return
        try:
            t = (time.perf_counter() - start) / seconds
            step(ease_out(t))
            if t < 1:
                widget.after(FRAME_MS, frame)
            elif done:
                done()
        except tk.TclError:
            pass  # la ventana se cerró en el medio

    frame()


def appear(window: tk.Misc, x: int | None = None, y: int | None = None, rise: int = 14, seconds: float = 0.2,
           alpha: float = 1.0, alive: Callable[[], bool] | None = None) -> None:
    """Aparece desvaneciéndose y subiendo un poco hasta (x, y) (si se da la posición)."""
    window.attributes("-alpha", 0.0)
    if x is not None and y is not None:
        window.geometry(f"+{x}+{y + rise}")

    def step(p: float) -> None:
        window.attributes("-alpha", alpha * p)
        if x is not None and y is not None:
            window.geometry(f"+{x}+{int(round(y + rise * (1 - p)))}")

    animate(window, seconds, step, alive=alive)


def vanish(window: tk.Misc, then: Callable[[], None], seconds: float = 0.12, alpha: float = 1.0,
           alive: Callable[[], bool] | None = None) -> None:
    """Se desvanece y después `then` (por ejemplo, esconderla)."""

    def step(p: float) -> None:
        window.attributes("-alpha", alpha * (1 - p))

    def done() -> None:
        then()
        window.attributes("-alpha", alpha)

    animate(window, seconds, step, done, alive)
