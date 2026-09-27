"""Bubble se adapta a la PC del jugador.

- Detecta el procesador (hilos) y la placa de video. Con GPU se captura la pantalla por GPU (ver capture/screen).
- Mide cuánto le cuesta de verdad cada lectura en esta PC y ajusta cada cuánto lee el chat y busca burbujas, para
  usar como máximo una parte de un núcleo: en una PC potente lee más seguido (más fluido); en una modesta, espacia
  las lecturas para no quitarle rendimiento a Roblox.
"""

from __future__ import annotations

import os
import time
import winreg
from dataclasses import dataclass, field

# Parte de UN núcleo que puede usar cada tarea, según la PC (el resto queda para Roblox).
SHARES = {
    "alta": {"chat": 0.25, "bubbles": 0.40},
    "media": {"chat": 0.15, "bubbles": 0.25},
    "baja": {"chat": 0.08, "bubbles": 0.12},
}


def _registry(path: str, name: str) -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            return str(winreg.QueryValueEx(key, name)[0]).strip()
    except OSError:
        return ""


def _gpus_from_registry() -> list[str]:
    names = []
    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    for index in range(8):
        name = _registry(rf"{base}\{index:04d}", "DriverDesc")
        if name and name not in names:
            names.append(name)
    return names


# Con más margen de CPU se lee más seguido (más rápido y fluido); con menos, se espacia.
MIN_INTERVAL_FACTOR = {"alta": 0.7, "media": 1.0, "baja": 1.5}


@dataclass
class Hardware:
    cpu: str
    threads: int
    gpus: list[str] = field(default_factory=list)
    gpu_capture: bool = False
    forced_tier: str = ""  # el jugador eligió el modo a mano

    @property
    def tier(self) -> str:
        if self.forced_tier in SHARES:
            return self.forced_tier
        if self.threads >= 12:
            return "alta"
        return "media" if self.threads >= 6 else "baja"

    def share(self, task: str) -> float:
        return SHARES[self.tier][task]

    def pacer(self, task: str, base_interval_s: float, max_s: float) -> Pacer:
        return Pacer(self.share(task), base_interval_s * MIN_INTERVAL_FACTOR[self.tier], max_s)

    def summary(self) -> str:
        gpu = self.gpus[0] if self.gpus else "sin GPU detectada"
        capture = "captura por GPU" if self.gpu_capture else "captura por CPU"
        return f"{self.cpu or 'CPU'} ({self.threads} hilos) · {gpu} · {capture} · modo {self.tier}"


def detect_hardware(gpu_capture: bool = True, forced_tier: str = "auto") -> Hardware:
    """Procesador y placas de video. Con `gpu_capture` se prueba (una vez) la captura por GPU."""
    from .capture import screen

    screen.set_gpu_capture(gpu_capture)
    cpu = _registry(r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "ProcessorNameString")
    gpu = screen.gpu_screen()
    gpus = list(gpu.adapters) if gpu is not None else []
    for name in _gpus_from_registry():
        if name not in gpus:
            gpus.append(name)
    return Hardware(" ".join(cpu.split()), os.cpu_count() or 4, gpus, gpu is not None,
                    forced_tier if forced_tier in SHARES else "")


class Pacer:
    """Ritmo adaptativo de una tarea que se repite: mide lo que cuesta cada vuelta y calcula la espera para que la
    tarea use como máximo `share` de un núcleo, entre `min_s` y `max_s` de espera."""

    def __init__(self, share: float, min_s: float, max_s: float) -> None:
        self.share = share
        self.min_s = min_s
        self.max_s = max_s
        self._cost: float | None = None

    def record(self, seconds: float) -> None:
        # Promedio suave: una lectura cara aislada (el OCR de un mensaje nuevo) no frena todo.
        self._cost = seconds if self._cost is None else 0.8 * self._cost + 0.2 * seconds

    @property
    def cost(self) -> float:
        return self._cost or 0.0

    @property
    def sleep(self) -> float:
        wanted = self.cost * (1 - self.share) / self.share
        return min(self.max_s, max(self.min_s, wanted))


class Stopwatch:
    """Tiempo de CPU del hilo actual (lo que cuesta de verdad, sin contar esperas) más tiempo real de lo que corre
    en otros hilos del sistema (el OCR de Windows)."""

    def __init__(self) -> None:
        self.seconds = 0.0

    def cpu(self, function, *args):
        start = time.thread_time()
        try:
            return function(*args)
        finally:
            self.seconds += time.thread_time() - start
