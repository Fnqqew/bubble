"""Funciones de Windows vía ctypes: ventana de Roblox, foco, teclado simulado, portapapeles y hotkeys.

Nada de esto toca el proceso de Roblox: solo usa APIs públicas de ventanas y entrada,
igual que un usuario o cualquier app de accesibilidad.
"""

from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable

from .geometry import Rect

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

ULONG_PTR = ctypes.c_size_t
ROBLOX_PROCESS = "robloxplayerbeta.exe"

# ---------- firmas (necesarias en 64 bits para no truncar handles) ----------
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetParent.restype = wintypes.HWND
user32.GetParent.argtypes = [wintypes.HWND]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.GetClipboardData.restype = ctypes.c_void_p
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.SetClipboardData.restype = ctypes.c_void_p
user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
user32.VkKeyScanW.restype = ctypes.c_short
user32.VkKeyScanW.argtypes = [wintypes.WCHAR]
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
user32.SetTimer.restype = ctypes.c_size_t
user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
kernel32.GlobalFree.argtypes = [ctypes.c_void_p]


def enable_dpi_awareness() -> None:
    """Coordenadas en píxeles reales (captura, overlay y ventana de Roblox coinciden)."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        user32.SetProcessDPIAware()


def set_app_id(app_id: str) -> None:
    """Para que la barra de tareas muestre el ícono propio en vez del de Python."""
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)


# ---------- ventana de Roblox ----------
def _process_name(pid: int) -> str:
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(512)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return os.path.basename(buffer.value)
        return ""
    finally:
        kernel32.CloseHandle(handle)


def client_rect(hwnd: int) -> Rect:
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    origin = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    return Rect(origin.x, origin.y, rect.right, rect.bottom)


def find_roblox_window() -> int | None:
    """La ventana visible más grande de RobloxPlayerBeta.exe (o None si Roblox no está abierto)."""
    found: list[tuple[int, int]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if _process_name(pid.value).lower() == ROBLOX_PROCESS:
                rect = client_rect(hwnd)
                found.append((rect.width * rect.height, hwnd))
        return True

    user32.EnumWindows(callback, 0)
    return max(found)[1] if found else None


def is_roblox_window(hwnd: int | None) -> bool:
    if not hwnd:
        return False
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return _process_name(pid.value).lower() == ROBLOX_PROCESS


def roblox_is_foreground() -> bool:
    """True si la ventana activa es Roblox (no minimizado ni en segundo plano)."""
    hwnd = user32.GetForegroundWindow()
    return is_roblox_window(hwnd) and not user32.IsIconic(hwnd)


def toplevel_hwnd(tk_widget) -> int:
    """HWND de la ventana de nivel superior de un Toplevel de Tk."""
    return user32.GetParent(tk_widget.winfo_id()) or tk_widget.winfo_id()


def force_foreground(hwnd: int) -> bool:
    if user32.GetForegroundWindow() == hwnd:
        return True
    foreground = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    my_thread = kernel32.GetCurrentThreadId()
    attached = bool(fg_thread and fg_thread != my_thread and user32.AttachThreadInput(my_thread, fg_thread, True))
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(my_thread, fg_thread, False)
    return user32.GetForegroundWindow() == hwnd


# ---------- ventanas overlay ----------
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x20
WS_EX_TOOLWINDOW = 0x80
WS_EX_LAYERED = 0x80000
WS_EX_NOACTIVATE = 0x08000000


def make_overlay(hwnd: int, click_through: bool = True, hide_from_capture: bool = False) -> None:
    """Ventana que no roba el foco, no aparece en la barra de tareas y (opcional) deja pasar los clics.

    Con `hide_from_capture`, la ventana no sale en las capturas de pantalla (Windows 10 2004+): así el OCR
    sigue leyendo el chat original de Roblox que está debajo de las traducciones.
    """
    style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    style |= WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    if click_through:
        style |= WS_EX_TRANSPARENT
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style)
    if hide_from_capture:
        user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)


WDA_EXCLUDEFROMCAPTURE = 0x11
user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]


# ---------- teclado simulado ----------
class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x2
KEYEVENTF_UNICODE = 0x4
VK_SHIFT, VK_CONTROL, VK_MENU, VK_RETURN, VK_ESCAPE = 0x10, 0x11, 0x12, 0x0D, 0x1B
VK_A, VK_C, VK_V = 0x41, 0x43, 0x56


def _send(events: list[tuple[int, int, int]]) -> None:
    inputs = (_INPUT * len(events))()
    for i, (vk, scan, flags) in enumerate(events):
        inputs[i].type = INPUT_KEYBOARD
        inputs[i].u.ki = _KEYBDINPUT(vk, scan, flags, 0, 0)
    user32.SendInput(len(events), inputs, ctypes.sizeof(_INPUT))


def press_keys(*vks: int) -> None:
    """Presiona una combinación (ej. Ctrl+V) y la suelta en orden inverso."""
    down = [(vk, user32.MapVirtualKeyW(vk, 0), 0) for vk in vks]
    up = [(vk, scan, KEYEVENTF_KEYUP) for vk, scan, _ in reversed(down)]
    _send(down + up)


def type_unicode(text: str) -> None:
    events: list[tuple[int, int, int]] = []
    raw = text.encode("utf-16-le")
    for i in range(0, len(raw), 2):
        unit = int.from_bytes(raw[i:i + 2], "little")
        events += [(0, unit, KEYEVENTF_UNICODE), (0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)]
    _send(events)


VK_OEM_2 = 0xBF  # la tecla "/" de un teclado estadounidense
SCAN_SLASH = 0x35  # su posición física (en un teclado latinoamericano es la tecla "-")


def press_chat_key() -> None:
    """Abre el chat de Roblox: la tecla física "/" (la que Roblox escucha), sin Shift ni ninguna otra.

    Antes se apretaba el carácter "/", que en un teclado en español es Shift + 7: a Roblox le llegaba un Shift
    (activa o desactiva el Shift Lock y la cámara se mueve) y un 7 (cambia de herramienta).
    """
    _send([(VK_OEM_2, SCAN_SLASH, 0), (VK_OEM_2, SCAN_SLASH, KEYEVENTF_KEYUP)])


def press_enter() -> None:
    _send([(VK_RETURN, 0x1C, 0), (VK_RETURN, 0x1C, KEYEVENTF_KEYUP)])


def wait_modifiers_released(timeout_s: float = 1.0) -> None:
    """Espera a que no haya Shift, Ctrl, Alt ni Win apretados (se combinarían con las teclas que se envían).
    No se suelta nada a la fuerza: eso también le llegaría al juego."""
    deadline = time.monotonic() + timeout_s
    while _modifiers_down() and time.monotonic() < deadline:
        time.sleep(0.02)


def press_char(char: str) -> None:
    """Presiona la tecla física que produce `char` en la distribución de teclado actual."""
    scan = user32.VkKeyScanW(char)
    if scan == -1:
        type_unicode(char)
        return
    vk, shift_state = scan & 0xFF, (scan >> 8) & 0xFF
    modifiers = [m for bit, m in ((1, VK_SHIFT), (2, VK_CONTROL), (4, VK_MENU)) if shift_state & bit]
    press_keys(*modifiers, vk)


# ---------- portapapeles ----------
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x2


def _open_clipboard() -> None:
    for _ in range(20):
        if user32.OpenClipboard(None):
            return
        time.sleep(0.01)
    raise OSError("No se pudo abrir el portapapeles")


def get_clipboard_text() -> str | None:
    _open_clipboard()
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def set_clipboard_text(text: str) -> None:
    data = text.encode("utf-16-le") + b"\x00\x00"
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    pointer = kernel32.GlobalLock(handle)
    ctypes.memmove(pointer, data, len(data))
    kernel32.GlobalUnlock(handle)
    _open_clipboard()
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            raise OSError("No se pudo escribir en el portapapeles")
    finally:
        user32.CloseClipboard()


# ---------- atajos globales: teclas y botones del mouse ----------
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x1, 0x2, 0x4, 0x8
MODIFIERS = {"alt": MOD_ALT, "ctrl": MOD_CONTROL, "control": MOD_CONTROL, "shift": MOD_SHIFT, "win": MOD_WIN}
MOD_NOREPEAT = 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
NAMED_KEYS = {"space": 0x20, "enter": 0x0D, "tab": 0x09, "insert": 0x2D, "home": 0x24, "end": 0x23,
              "pageup": 0x21, "pagedown": 0x22, "pause": 0x13, "scrolllock": 0x91, "capslock": 0x14,
              "numlock": 0x90, "backspace": 0x08, "delete": 0x2E}
NAMED_KEYS_ES = {"space": "Espacio", "enter": "Enter", "tab": "Tab", "insert": "Insert", "home": "Inicio",
                 "end": "Fin", "pageup": "Re Pág", "pagedown": "Av Pág", "pause": "Pausa",
                 "scrolllock": "Bloq Despl", "capslock": "Bloq Mayús", "numlock": "Bloq Num",
                 "backspace": "Retroceso", "delete": "Supr"}
# Botón del mouse -> virtual key. Izquierdo y derecho no se permiten (romperían el juego).
MOUSE_BUTTONS = {"mouse3": 0x04, "mouse4": 0x05, "mouse5": 0x06}
MOUSE_NAMES = {"mouse3": "Clic de la rueda del mouse", "mouse4": "Botón lateral del mouse (atrás)",
               "mouse5": "Botón lateral del mouse (adelante)"}
_MODIFIER_VKS = {0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C}

user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetKeyNameTextW.argtypes = [ctypes.c_long, wintypes.LPWSTR, ctypes.c_int]


@dataclass(frozen=True)
class Binding:
    kind: str  # "key" | "mouse"
    modifiers: int
    vk: int


def _split_spec(spec: str) -> tuple[list[str], str]:
    parts = [p.strip() for p in spec.strip().split("+")]
    if len(parts) > 1 and parts[-1] == "":  # el propio '+' como tecla
        parts = parts[:-2] + ["+"]
    *mods, key = parts
    return mods, key


def parse_binding(spec: str) -> Binding:
    """'°', 'F8', 'ctrl+shift+t', 'mouse4', 'alt+mouse5', 'vk:DC' -> Binding.

    Un carácter suelto (°, ñ, |) se busca en la distribución de teclado actual: '°' en un teclado
    latinoamericano es Shift + la tecla a la izquierda del 1.
    """
    if not spec.strip():
        raise ValueError("Atajo vacío")
    mods, key = _split_spec(spec)
    modifiers = 0
    for mod in mods:
        if mod.lower() not in MODIFIERS:
            raise ValueError(f"Modificador desconocido: {mod}")
        modifiers |= MODIFIERS[mod.lower()]
    lower = key.lower()
    if lower in MOUSE_BUTTONS:
        return Binding("mouse", modifiers, MOUSE_BUTTONS[lower])
    if lower.startswith("f") and lower[1:].isdigit() and 1 <= int(lower[1:]) <= 24:
        return Binding("key", modifiers, 0x70 + int(lower[1:]) - 1)
    if lower in NAMED_KEYS:
        return Binding("key", modifiers, NAMED_KEYS[lower])
    if lower.startswith("vk:"):
        return Binding("key", modifiers, int(lower[3:], 16))
    if len(key) == 1 and key.isascii() and key.isalnum():
        return Binding("key", modifiers, ord(key.upper()))
    if len(key) == 1:
        scan = user32.VkKeyScanW(key)
        if scan == -1:
            raise ValueError(f"La tecla «{key}» no existe en tu distribución de teclado")
        shift_state = (scan >> 8) & 0xFF
        for bit, mod in ((1, MOD_SHIFT), (2, MOD_CONTROL), (4, MOD_ALT)):
            if shift_state & bit:
                modifiers |= mod
        return Binding("key", modifiers, scan & 0xFF)
    raise ValueError(f"Tecla desconocida: {key}")


def _key_name(vk: int) -> str:
    scan = user32.MapVirtualKeyW(vk, 0)
    buffer = ctypes.create_unicode_buffer(64)
    if scan and user32.GetKeyNameTextW(scan << 16, buffer, 64):
        return buffer.value
    return f"Tecla {vk:#04x}"


def describe_binding(spec: str) -> str:
    """Nombre legible del atajo: '°', 'F8', 'Ctrl + Shift + T', 'Botón lateral del mouse (atrás)'."""
    mods, key = _split_spec(spec)
    names = [{"ctrl": "Ctrl", "control": "Ctrl", "shift": "Shift", "alt": "Alt", "win": "Win"}.get(m.lower(), m)
             for m in mods]
    lower = key.lower()
    if lower in MOUSE_NAMES:
        names.append(MOUSE_NAMES[lower])
    elif lower in NAMED_KEYS_ES:
        names.append(NAMED_KEYS_ES[lower])
    elif lower.startswith("vk:"):
        names.append(_key_name(int(lower[3:], 16)))
    else:
        names.append(key.upper())
    return " + ".join(names)


def _modifiers_down() -> int:
    mods = 0
    for vk, mod in ((VK_SHIFT, MOD_SHIFT), (VK_CONTROL, MOD_CONTROL), (VK_MENU, MOD_ALT)):
        if user32.GetAsyncKeyState(vk) & 0x8000:
            mods |= mod
    if user32.GetAsyncKeyState(0x5B) & 0x8000 or user32.GetAsyncKeyState(0x5C) & 0x8000:
        mods |= MOD_WIN
    return mods


WM_TIMER = 0x0113


def _shift_only_from_char(spec: str, binding: Binding) -> bool:
    """El atajo es un carácter que se escribe con Shift (ej. '°' en un teclado latinoamericano)."""
    return len(spec.strip()) == 1 and binding.kind == "key" and binding.modifiers == MOD_SHIFT


class _KeyListener(threading.Thread):
    """Tecla: RegisterHotKey (Windows la entrega directo, sin procesar cada tecla en Python).

    - Con `active`, el atajo solo se registra mientras `active()` sea True (Roblox al frente): en otros
      programas (y en la barra de Bubble) la tecla escribe normalmente.
    - Si el atajo es un carácter con Shift ('°'), la misma tecla sin Shift también sirve: en Roblox, Shift
      activa el Shift Lock y mueve la cámara, y ese Shift le llega al juego antes que el atajo.
    """

    def __init__(self, spec: str, binding: Binding, callback: Callable[[], None],
                 active: Callable[[], bool] | None = None) -> None:
        super().__init__(name="bubble-hotkey", daemon=True)
        self.spec, self.binding, self.callback, self.active = spec, binding, callback, active
        self.bindings = [binding]
        if _shift_only_from_char(spec, binding):
            self.bindings.append(Binding("key", 0, binding.vk))
        self.error: str | None = None
        self.ready = threading.Event()
        self._thread_id = 0
        self._registered = False

    def _register(self) -> bool:
        ok = bool(user32.RegisterHotKey(None, 1, self.binding.modifiers | MOD_NOREPEAT, self.binding.vk))
        if ok:
            for number, extra in enumerate(self.bindings[1:], start=2):
                user32.RegisterHotKey(None, number, extra.modifiers | MOD_NOREPEAT, extra.vk)  # si falla, no importa
        self._registered = ok
        return ok

    def _unregister(self) -> None:
        for number in range(1, len(self.bindings) + 1):
            user32.UnregisterHotKey(None, number)
        self._registered = False

    def run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        if not self._register():
            self.error = f"El atajo {describe_binding(self.spec)} ya lo usa otro programa"
            self.ready.set()
            return
        if self.active and not self.active():
            self._unregister()
        self.ready.set()
        timer = user32.SetTimer(None, 0, 150, None) if self.active else 0
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    self.callback()
                elif msg.message == WM_TIMER and self.active:
                    wanted = self.active()
                    if wanted and not self._registered:
                        self._register()
                    elif not wanted and self._registered:
                        self._unregister()
        finally:
            if timer:
                user32.KillTimer(None, timer)
            self._unregister()

    def stop(self) -> None:
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)


class _MouseListener(threading.Thread):
    """Botón del mouse: se consulta su estado cada 15 ms (sin ganchos globales que agreguen lag al juego)."""

    def __init__(self, spec: str, binding: Binding, callback: Callable[[], None],
                 active: Callable[[], bool] | None = None) -> None:
        super().__init__(name="bubble-mouse", daemon=True)
        self.spec, self.binding, self.callback, self.active = spec, binding, callback, active
        self.error: str | None = None
        self.ready = threading.Event()
        self._stop_event = threading.Event()

    def run(self) -> None:
        self.ready.set()
        was_down = True  # no disparar si el botón ya estaba apretado al empezar
        while not self._stop_event.wait(0.015):
            down = bool(user32.GetAsyncKeyState(self.binding.vk) & 0x8000)
            if (down and not was_down and _modifiers_down() == self.binding.modifiers
                    and (self.active is None or self.active())):
                self.callback()
            was_down = down

    def stop(self) -> None:
        self._stop_event.set()


def start_trigger(spec: str, callback: Callable[[], None], active: Callable[[], bool] | None = None):
    """Arranca el atajo (tecla o botón del mouse). Devuelve el listener (con .error y .stop()).

    Con `active`, el atajo solo funciona (y solo se adueña de la tecla) mientras `active()` sea True."""
    binding = parse_binding(spec)
    listener_cls = _MouseListener if binding.kind == "mouse" else _KeyListener
    listener = listener_cls(spec, binding, callback, active)
    listener.start()
    listener.ready.wait(2)
    return listener


def capture_binding(cancel: threading.Event, timeout_s: float = 30.0) -> str | None:
    """Espera a que el usuario apriete una tecla o botón del mouse y devuelve su atajo ('°', 'mouse4'...).

    Esc cancela (None). Clic izquierdo/derecho se ignoran.
    """
    candidates = [vk for vk in range(0x03, 0xFF) if vk not in _MODIFIER_VKS and vk != VK_ESCAPE]
    deadline = time.monotonic() + timeout_s
    # Esperar a que se suelte todo (ej. el clic sobre el botón "Cambiar").
    while any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in candidates) and time.monotonic() < deadline:
        time.sleep(0.02)
    while not cancel.is_set() and time.monotonic() < deadline:
        if user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000:
            return None
        for vk in candidates:
            if user32.GetAsyncKeyState(vk) & 0x8000:
                return spec_for(vk, _modifiers_down())
        time.sleep(0.015)
    return None


def spec_for(vk: int, mods: int) -> str:
    """Atajo en texto para una tecla física + modificadores."""
    prefix = "".join(name + "+" for name, bit in (("ctrl", MOD_CONTROL), ("alt", MOD_ALT), ("win", MOD_WIN))
                     if mods & bit)
    shift = "shift+" if mods & MOD_SHIFT else ""
    for name, mouse_vk in MOUSE_BUTTONS.items():
        if vk == mouse_vk:
            return prefix + shift + name
    if 0x70 <= vk <= 0x87:
        return prefix + shift + f"F{vk - 0x70 + 1}"
    for name, named_vk in NAMED_KEYS.items():
        if vk == named_vk:
            return prefix + shift + name
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return prefix + shift + chr(vk).lower()
    # Teclas de símbolos: usar el carácter que producen (ej. Shift + tecla -> '°').
    char = _char_for(vk, bool(mods & MOD_SHIFT))
    if char and not mods & (MOD_CONTROL | MOD_ALT | MOD_WIN):
        try:
            if parse_binding(char) == Binding("key", mods & MOD_SHIFT, vk):
                return char
        except ValueError:
            pass
    return prefix + shift + f"vk:{vk:02X}"


def _char_for(vk: int, shift: bool) -> str:
    state = (ctypes.c_ubyte * 256)()
    if shift:
        state[VK_SHIFT] = 0x80
    buffer = ctypes.create_unicode_buffer(8)
    scan = user32.MapVirtualKeyW(vk, 0)
    count = user32.ToUnicode(vk, scan, state, buffer, 8, 0x4)  # 0x4: no alterar teclas muertas
    text = buffer.value[:count] if count > 0 else ""
    return text if len(text) == 1 and text.isprintable() and not text.isspace() else ""


