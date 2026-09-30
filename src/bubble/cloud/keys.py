"""Clave del servicio en la nube, guardada cifrada con Windows (DPAPI): solo el usuario de Windows que la guardó puede
leerla. No queda en texto plano en ningún archivo.
"""

from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _Blob:
    buffer = ctypes.create_string_buffer(data, len(data))
    blob = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob._keep = buffer  # evita que se libere antes de usarlo
    return blob


def _out(blob: _Blob) -> bytes:
    data = ctypes.string_at(blob.data, blob.size)
    ctypes.windll.kernel32.LocalFree(blob.data)
    return data


def protect(text: str) -> str:
    source, result = _blob(text.encode("utf-8")), _Blob()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(source), "Bubble", None, None, None, 0,
                                                   ctypes.byref(result)):
        raise OSError("No se pudo cifrar la clave")
    return base64.b64encode(_out(result)).decode("ascii")


def unprotect(token: str) -> str:
    source, result = _blob(base64.b64decode(token)), _Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 0,
                                                     ctypes.byref(result)):
        raise OSError("No se pudo leer la clave (¿se guardó con otro usuario de Windows?)")
    return _out(result).decode("utf-8")


def save_key(key: str) -> None:
    from ..state import update_state

    update_state(pro_key=protect(key.strip()) if key.strip() else "")


def load_key() -> str:
    from ..state import load_state

    token = load_state().get("pro_key", "")
    if not token:
        return ""
    try:
        return unprotect(token)
    except (OSError, ValueError):
        return ""
