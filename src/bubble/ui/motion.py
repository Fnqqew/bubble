"""Animaciones cortas y suaves: la ventana y la barra de escritura aparecen deslizándose y se cierran desvaneciéndose.
Todo corre en el hilo de la ventana (con `after`), a unos 60 cuadros por segundo y en menos de 0,25 s: son
perceptibles, pero no hacen esperar al jugador.
"""

from __future__ import annotations

import time
import tkinter as tk
from typing import Callable

FRAME_MS = 15


def ease_out(t: float) -> float:
    """Curva de suavizado: arranca rápido y desacelera al llegar."""
    t = min(1.0, max(0.0, t))
    return 1 - (1 - t) ** 3


def animate(widget: tk.Misc, seconds: float, step: Callable[[float], None],
            done: Callable[[], None] | None = None, alive: Callable[[], bool] | None = None) -> None:
    """Llama a `step(avance 0→1, ya suavizado)` en cada cuadro durante `seconds` y, al final, a `done`. Si `alive` deja
    de ser verdadero (otra animación la reemplazó), se interrumpe sin modificar nada.
    """
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
            pass  # la ventana se cerró durante la animación

    frame()


def appear(window: tk.Misc, x: int | None = None, y: int | None = None, rise: int = 14, seconds: float = 0.2,
           alpha: float = 1.0, alive: Callable[[], bool] | None = None) -> None:
    """Aparece con fundido y un leve desplazamiento ascendente hasta (x, y), si se indica la posición."""
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
    """Se desvanece y luego ejecuta `then` (por ejemplo, ocultar la ventana)."""

    def step(p: float) -> None:
        window.attributes("-alpha", alpha * (1 - p))

    def done() -> None:
        then()
        window.attributes("-alpha", alpha)

    animate(window, seconds, step, done, alive)
