"""Ventanas con transparencia por píxel (UpdateLayeredWindow) para dibujar traducciones sobre Roblox.

Permiten bordes redondeados y suaves, sombras y fondos semitransparentes (algo que una ventana de Tk no
puede), y son más rápidas: mostrar una imagen es una sola llamada a Windows y moverla no la redibuja.
Además no aparecen en las capturas de pantalla (así el OCR sigue leyendo el chat original de debajo) y
dejan pasar los clics.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PIL import Image, ImageChops

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

HANDLE = ctypes.c_void_p
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

WS_POPUP = 0x80000000
WS_EX_TOPMOST, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW = 0x8, 0x20, 0x80
WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x80000, 0x08000000
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
SWP_NOSIZE, SWP_NOACTIVATE = 0x1, 0x10
HWND_TOPMOST = HANDLE(-1)
ULW_ALPHA, AC_SRC_OVER, AC_SRC_ALPHA = 0x2, 0x0, 0x1
WDA_EXCLUDEFROMCAPTURE = 0x11
CLASS_NAME = "BubbleTranslationPatch"


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", HANDLE),
        ("hIcon", HANDLE), ("hCursor", HANDLE), ("hbrBackground", HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", HANDLE),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.RegisterClassExW.restype = wintypes.ATOM
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, HANDLE, HANDLE, HANDLE, HANDLE,
]
user32.CreateWindowExW.restype = HANDLE
user32.DestroyWindow.argtypes = [HANDLE]
user32.ShowWindow.argtypes = [HANDLE, ctypes.c_int]
user32.SetWindowPos.argtypes = [HANDLE, HANDLE, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetWindowDisplayAffinity.argtypes = [HANDLE, wintypes.DWORD]
user32.GetDC.argtypes = [HANDLE]
user32.GetDC.restype = HANDLE
user32.ReleaseDC.argtypes = [HANDLE, HANDLE]
user32.UpdateLayeredWindow.argtypes = [
    HANDLE, HANDLE, ctypes.POINTER(wintypes.POINT), ctypes.POINTER(wintypes.SIZE), HANDLE,
    ctypes.POINTER(wintypes.POINT), wintypes.DWORD, ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD,
]
gdi32.CreateCompatibleDC.argtypes = [HANDLE]
gdi32.CreateCompatibleDC.restype = HANDLE
gdi32.CreateDIBSection.argtypes = [HANDLE, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), HANDLE, wintypes.DWORD]
gdi32.CreateDIBSection.restype = HANDLE
gdi32.SelectObject.argtypes = [HANDLE, HANDLE]
gdi32.SelectObject.restype = HANDLE
gdi32.DeleteObject.argtypes = [HANDLE]
gdi32.DeleteDC.argtypes = [HANDLE]
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = HANDLE

_class_registered = False
# DefWindowProcW nativo como procedimiento de la ventana: no hay código Python por cada mensaje.
_WNDPROC = WNDPROC(ctypes.cast(user32.DefWindowProcW, ctypes.c_void_p).value)


def _register_class() -> None:
    global _class_registered
    if _class_registered:
        return
    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.lpfnWndProc = _WNDPROC
    wc.hInstance = kernel32.GetModuleHandleW(None)
    wc.lpszClassName = CLASS_NAME
    user32.RegisterClassExW(ctypes.byref(wc))  # si ya estaba registrada, falla sin problema
    _class_registered = True


def to_premultiplied_bgra(image: Image.Image) -> bytes:
    """RGBA -> BGRA con alfa premultiplicado (lo que pide UpdateLayeredWindow)."""
    r, g, b, a = image.convert("RGBA").split()
    r, g, b = (ImageChops.multiply(channel, a) for channel in (r, g, b))
    return Image.merge("RGBA", (b, g, r, a)).tobytes()


class LayeredWindow:
    """Ventana siempre arriba, sin foco, que deja pasar los clics y no sale en capturas."""

    def __init__(self) -> None:
        _register_class()
        style = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST
        self.hwnd = user32.CreateWindowExW(style, CLASS_NAME, "", WS_POPUP, 0, 0, 1, 1, None, None,
                                           kernel32.GetModuleHandleW(None), None)
        if not self.hwnd:
            raise OSError(f"No se pudo crear la ventana de la traducción ({ctypes.get_last_error()})")
        user32.SetWindowDisplayAffinity(self.hwnd, WDA_EXCLUDEFROMCAPTURE)
        self.visible = False
        self.position = (0, 0)
        self.size = (0, 0)

    def update(self, image: Image.Image, x: int, y: int) -> None:
        """Muestra `image` (RGBA, con transparencia) en (x, y) de la pantalla."""
        width, height = image.size
        header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        info = BITMAPINFO(header)
        screen_dc = user32.GetDC(None)
        memory_dc = gdi32.CreateCompatibleDC(screen_dc)
        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(memory_dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        try:
            data = to_premultiplied_bgra(image)
            ctypes.memmove(bits, data, len(data))
            previous = gdi32.SelectObject(memory_dc, bitmap)
            blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
            user32.UpdateLayeredWindow(
                self.hwnd, screen_dc, ctypes.byref(wintypes.POINT(x, y)), ctypes.byref(wintypes.SIZE(width, height)),
                memory_dc, ctypes.byref(wintypes.POINT(0, 0)), 0, ctypes.byref(blend), ULW_ALPHA,
            )
            gdi32.SelectObject(memory_dc, previous)
        finally:
            gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(memory_dc)
            user32.ReleaseDC(None, screen_dc)
        self.position, self.size = (x, y), (width, height)
        self._show()

    def move(self, x: int, y: int) -> None:
        if (x, y) != self.position:
            user32.SetWindowPos(self.hwnd, HWND_TOPMOST, x, y, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE)
            self.position = (x, y)
        self._show()

    def _show(self) -> None:
        if not self.visible:
            user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
            user32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE | 0x2)
            self.visible = True

    def hide(self) -> None:
        if self.visible:
            user32.ShowWindow(self.hwnd, SW_HIDE)
            self.visible = False

    def destroy(self) -> None:
        if self.hwnd:
            user32.DestroyWindow(self.hwnd)
            self.hwnd = None
