"""Que las traducciones salgan en tus capturas de pantalla (para mandar ejemplos).

Las traducciones son ventanas invisibles para las capturas: así el OCR lee el chat original que queda debajo. Cuando
sacás una captura con Windows (Impr Pant, Win + Shift + S o Win + Impr Pant), se vuelven visibles para las capturas
un momento y, mientras tanto, el OCR no lee (leería las traducciones en vez del chat).

Las teclas llegan por "raw input": Windows avisa de cada tecla sin esperar a Bubble (un gancho de teclado sí la
haría esperar y sumaría demora a lo que apretás en el juego). La tecla Win se aprieta antes que la otra, así que con
ella las traducciones ya se ven cuando Windows saca la captura; Impr Pant sola abre la Herramienta Recortes, que tarda
más que eso en sacarla.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from ctypes import wintypes
from typing import Callable

from . import layered, win32
from .capture import screen

log = logging.getLogger(__name__)
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

VK_SHIFT, VK_S, VK_SNAPSHOT, VK_LWIN, VK_RWIN = 0x10, 0x53, 0x2C, 0x5B, 0x5C
WIN_HOLD_S = 0.5  # después de soltar Win
SHOT_HOLD_S = 3.0  # después de Impr Pant o Win + Shift + S: la Herramienta Recortes tarda en abrirse
SNIP_HOLD_S = 0.4  # después de cerrar la Herramienta Recortes
SETTLE_S = 0.06  # Windows aplica el cambio en el próximo cuadro de la pantalla
TICK_MS = 50
# La Herramienta Recortes (Windows 11) y el recorte de Windows 10.
SNIPPING = {"snippingtool.exe", "screenclippinghost.exe", "screensketch.exe"}

WM_INPUT, WM_TIMER, WM_QUIT = 0x00FF, 0x0113, 0x0012
RIDEV_INPUTSINK, RID_INPUT, RIM_TYPEKEYBOARD, RI_KEY_BREAK = 0x100, 0x10000003, 1, 0x1
HWND_MESSAGE = wintypes.HWND(-3)


def hold_for(vk: int, down: bool, win: bool, shift: bool) -> float:
    """Cuántos segundos mostrar las traducciones en capturas por esta tecla (0: no es para sacar una captura)."""
    if not down:
        return 0.0
    if vk in (VK_LWIN, VK_RWIN):
        return WIN_HOLD_S
    if vk == VK_SNAPSHOT or (vk == VK_S and win and shift):
        return SHOT_HOLD_S
    return 0.0


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT), ("dwFlags", wintypes.DWORD),
                ("hwndTarget", wintypes.HWND)]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", wintypes.DWORD), ("dwSize", wintypes.DWORD), ("hDevice", wintypes.HANDLE),
                ("wParam", wintypes.WPARAM)]


class RAWKEYBOARD(ctypes.Structure):
    _fields_ = [("MakeCode", wintypes.USHORT), ("Flags", wintypes.USHORT), ("Reserved", wintypes.USHORT),
                ("VKey", wintypes.USHORT), ("Message", wintypes.UINT), ("ExtraInformation", wintypes.ULONG)]


class RAWINPUT(ctypes.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("keyboard", RAWKEYBOARD), ("_rest", ctypes.c_byte * 16)]


user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HANDLE,
                                   wintypes.HANDLE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT]
user32.GetRawInputData.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPVOID, ctypes.POINTER(wintypes.UINT),
                                   wintypes.UINT]
user32.GetRawInputData.restype = wintypes.UINT
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
user32.SetTimer.restype = ctypes.c_size_t
user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short


def _held(*vks: int) -> bool:
    return any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in vks)


class ScreenshotKeys(threading.Thread):
    """Mira las teclas de captura y hace visibles las traducciones para las capturas mientras hace falta.

    `active()` dice si ahora hay traducciones en pantalla (Roblox al frente): si no, no hace nada."""

    def __init__(self, active: Callable[[], bool]) -> None:
        super().__init__(name="bubble-capturas", daemon=True)
        self.active = active
        self.error = ""
        self.showing = False  # las traducciones se ven en las capturas
        self._until = 0.0
        self._timer = 0
        self._thread_id = 0
        self._ready = threading.Event()

    # ---------------------------------------------------------------- hilo
    def run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        hwnd = user32.CreateWindowExW(0, "STATIC", "", 0, 0, 0, 0, 0, HWND_MESSAGE, None, None, None)
        device = RAWINPUTDEVICE(0x01, 0x06, RIDEV_INPUTSINK, hwnd)  # teclado, también con Roblox al frente
        if not hwnd or not user32.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(device)):
            self.error = f"No se pudo mirar el teclado ({ctypes.get_last_error()})"
            log.warning(self.error)
            self._ready.set()
            return
        self._ready.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_INPUT:
                    self._on_key(msg.lParam)
                    user32.DispatchMessageW(ctypes.byref(msg))  # Windows libera los datos de la tecla
                elif msg.message == WM_TIMER:
                    self._tick()
        except Exception:  # noqa: BLE001 - sin esto, las traducciones igual se ven en el juego
            log.exception("Falló la detección de capturas")
        finally:
            self._show(False)
            device = RAWINPUTDEVICE(0x01, 0x06, 0x1, None)  # RIDEV_REMOVE
            user32.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(device))
            user32.DestroyWindow(hwnd)

    def start(self) -> None:
        super().start()
        self._ready.wait(2)

    def stop(self) -> None:
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            # Se espera a que suelte el teclado: si no, al volver a prenderlo, el hilo viejo le sacaría el teclado
            # al nuevo (Windows lo registra uno por programa).
            self.join(1)

    # ---------------------------------------------------------------- teclas
    def _on_key(self, handle: int) -> None:
        data = RAWINPUT()
        size = wintypes.UINT(ctypes.sizeof(data))
        got = user32.GetRawInputData(handle, RID_INPUT, ctypes.byref(data), ctypes.byref(size),
                                     ctypes.sizeof(RAWINPUTHEADER))
        if got in (0, 0xFFFFFFFF) or data.header.dwType != RIM_TYPEKEYBOARD:
            return
        down = not data.keyboard.Flags & RI_KEY_BREAK
        hold = hold_for(data.keyboard.VKey, down, _held(VK_LWIN, VK_RWIN), _held(VK_SHIFT))
        if hold and (self.showing or self.active()):
            self._until = max(self._until, time.monotonic() + hold)
            self._update()

    def _tick(self) -> None:
        now = time.monotonic()
        if _held(VK_LWIN, VK_RWIN):
            self._until = max(self._until, now + WIN_HOLD_S)
        if win32.foreground_process() in SNIPPING:
            self._until = max(self._until, now + SNIP_HOLD_S)  # sacando el recorte (o recién sacado)
        self._update()

    def _update(self) -> None:
        wanted = time.monotonic() < self._until
        if wanted != self.showing:
            self._show(wanted)

    def _show(self, visible: bool) -> None:
        if visible == self.showing:
            return
        if visible:
            screen.pause_reading(True)  # primero se deja de leer, después se hacen visibles
            layered.set_capturable(True)
            self._timer = user32.SetTimer(None, 0, TICK_MS, None)
        else:
            layered.set_capturable(False)
            time.sleep(SETTLE_S)  # que el próximo cuadro ya no las tenga antes de volver a leer
            screen.pause_reading(False)
            if self._timer:
                user32.KillTimer(None, self._timer)
                self._timer = 0
        self.showing = visible
