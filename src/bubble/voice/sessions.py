"""Qué programas están sonando (lo que muestra el Mezclador de volumen de Windows), con su nivel de ahora.

Sirve para encontrar de qué proceso sale el sonido de Roblox: no siempre es RobloxPlayerBeta.exe (el chat de voz, por
ejemplo, puede sonar desde otro proceso). Se usa COM a mano con ctypes, como en process_audio.py.
"""

from __future__ import annotations

import ctypes
from ctypes import POINTER, byref, c_void_p, wintypes
from dataclasses import dataclass

from .process_audio import GUID, HRESULT, _call, _guid, _release

CLSID_MMDeviceEnumerator = _guid("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_IMMDeviceEnumerator = _guid("A95664D2-9614-4F35-A746-DE8DB63617E6")
IID_IAudioSessionManager2 = _guid("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F")
IID_IAudioSessionControl2 = _guid("bfb7ff88-7239-4fc9-8fa2-07c950be9c6d")
IID_IAudioMeterInformation = _guid("C02216F6-8C67-4B5B-9D00-D008E73E0064")
CLSCTX_ALL = 23


@dataclass
class Session:
    pid: int
    name: str
    active: bool  # AudioSessionStateActive
    peak: float  # nivel de ahora (0 a 1)


def _process_name(pid: int) -> str:
    from .. import win32

    return win32._process_name(pid).lower() if pid else "sonidos del sistema"


def sessions(device_id: str | None = None) -> list[Session]:
    """Todas las sesiones de audio del parlante predeterminado (o del dispositivo `device_id`)."""
    from .audio import com_ready

    com_ready()
    ole32 = ctypes.windll.ole32
    ole32.CoCreateInstance.argtypes = [POINTER(GUID), c_void_p, wintypes.DWORD, POINTER(GUID), POINTER(c_void_p)]
    ole32.CoCreateInstance.restype = HRESULT
    enumerator = c_void_p()
    ole32.CoCreateInstance(byref(CLSID_MMDeviceEnumerator), None, CLSCTX_ALL, byref(IID_IMMDeviceEnumerator),
                           byref(enumerator))
    device, manager, listing = c_void_p(), c_void_p(), c_void_p()
    found: list[Session] = []
    try:
        if device_id:
            _call(enumerator, 5, device_id, byref(device), argtypes=(wintypes.LPCWSTR, POINTER(c_void_p)))  # GetDevice
        else:
            _call(enumerator, 4, 0, 1, byref(device), argtypes=(ctypes.c_int, ctypes.c_int, POINTER(c_void_p)))
        _call(device, 3, byref(IID_IAudioSessionManager2), CLSCTX_ALL, None, byref(manager),
              argtypes=(POINTER(GUID), wintypes.DWORD, c_void_p, POINTER(c_void_p)))
        _call(manager, 5, byref(listing), argtypes=(POINTER(c_void_p),))
        count = ctypes.c_int()
        _call(listing, 3, byref(count), argtypes=(POINTER(ctypes.c_int),))
        for index in range(count.value):
            control, control2, meter = c_void_p(), c_void_p(), c_void_p()
            try:
                _call(listing, 4, index, byref(control), argtypes=(ctypes.c_int, POINTER(c_void_p)))
                _call(control, 0, byref(IID_IAudioSessionControl2), byref(control2),
                      argtypes=(POINTER(GUID), POINTER(c_void_p)))
                state, pid, peak = ctypes.c_int(), wintypes.DWORD(), ctypes.c_float()
                _call(control2, 3, byref(state), argtypes=(POINTER(ctypes.c_int),))
                _call(control2, 14, byref(pid), argtypes=(POINTER(wintypes.DWORD),))
                _call(control, 0, byref(IID_IAudioMeterInformation), byref(meter),
                      argtypes=(POINTER(GUID), POINTER(c_void_p)))
                _call(meter, 3, byref(peak), argtypes=(POINTER(ctypes.c_float),))
                found.append(Session(pid.value, _process_name(pid.value), state.value == 1, float(peak.value)))
            except OSError:
                continue
            finally:
                for obj in (meter, control2, control):
                    _release(obj)
    finally:
        for obj in (listing, manager, device, enumerator):
            _release(obj)
    return found
