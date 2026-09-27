"""El sonido de un solo programa (Roblox): ni YouTube, ni Discord, ni música, ni los avisos de Windows.

Windows 11 (y 10 desde la versión 20348) deja capturar lo que suena de un proceso ("process loopback"). Se usa por
COM, a mano con ctypes (como el resto de Bubble): se activa un IAudioClient especial para el proceso de Roblox y se lee
en bloques de 10 ms. Si Roblox no suena, Windows no manda nada: se completa con silencio, al ritmo real, así el oído de
Bubble (el detector de voz, los tiempos de las frases) funciona igual que con el parlante entero.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
import uuid
from ctypes import POINTER, byref, c_void_p, wintypes

import numpy as np

log = logging.getLogger(__name__)
HRESULT = ctypes.HRESULT


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]


def _guid(text: str) -> GUID:
    return GUID.from_buffer_copy(uuid.UUID(text).bytes_le)


IID_IUnknown = _guid("00000000-0000-0000-C000-000000000046")
IID_IAgileObject = _guid("94ea2b94-e9cc-49e0-c0ff-ee64ca8f5b90")
IID_Handler = _guid("41D949AB-9862-444A-80F6-C261334DA5EB")  # IActivateAudioInterfaceCompletionHandler
IID_IAudioClient = _guid("1CB9AD4C-DBFA-4c32-B178-C2F568A703B2")
IID_IAudioCaptureClient = _guid("C8ADBD64-E71E-48a0-A4DE-185C395CD317")

VT_BLOB = 65
ACTIVATION_PROCESS_LOOPBACK = 1
INCLUDE_PROCESS_TREE = 0
LOOPBACK, EVENTCALLBACK = 0x00020000, 0x00040000
AUTOCONVERTPCM, SRC_DEFAULT_QUALITY = 0x80000000, 0x08000000
BUFFER_SILENT = 0x2
WAVE_FORMAT_IEEE_FLOAT = 3
E_NOINTERFACE = -2147467262


class BLOB(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.ULONG), ("pBlobData", c_void_p)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", wintypes.USHORT), ("r1", wintypes.USHORT), ("r2", wintypes.USHORT), ("r3", wintypes.USHORT),
                ("blob", BLOB)]


class LoopbackParams(ctypes.Structure):  # AUDIOCLIENT_ACTIVATION_PARAMS con AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS
    _fields_ = [("ActivationType", ctypes.c_int), ("TargetProcessId", wintypes.DWORD),
                ("ProcessLoopbackMode", ctypes.c_int)]


class WAVEFORMATEX(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD), ("nSamplesPerSec", wintypes.DWORD),
                ("nAvgBytesPerSec", wintypes.DWORD), ("nBlockAlign", wintypes.WORD), ("wBitsPerSample", wintypes.WORD),
                ("cbSize", wintypes.WORD)]


_QI = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, POINTER(GUID), POINTER(c_void_p))
_REF = ctypes.WINFUNCTYPE(wintypes.ULONG, c_void_p)
_DONE = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, c_void_p)


class _HandlerVtbl(ctypes.Structure):
    _fields_ = [("QueryInterface", _QI), ("AddRef", _REF), ("Release", _REF), ("ActivateCompleted", _DONE)]


class _Handler(ctypes.Structure):
    _fields_ = [("lpVtbl", POINTER(_HandlerVtbl))]


def _call(obj: c_void_p, index: int, *args, argtypes=(), restype=HRESULT):
    """Método `index` de la tabla de un objeto COM."""
    table = ctypes.cast(obj, POINTER(POINTER(c_void_p)))[0]
    return ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(table[index])(obj, *args)


def _release(obj: c_void_p | None) -> None:
    if obj:
        _call(obj, 2, restype=wintypes.ULONG)


class CompletionHandler:
    """IActivateAudioInterfaceCompletionHandler hecho en Python: avisa cuando Windows terminó de activar."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self._keep = (_QI(self._query), _REF(lambda _this: 1), _REF(lambda _this: 1), _DONE(self._completed))
        self._vtbl = _HandlerVtbl(*self._keep)
        self.obj = _Handler(ctypes.pointer(self._vtbl))

    def _query(self, this, riid, out) -> int:
        wanted = bytes(riid.contents)
        if wanted in (bytes(IID_IUnknown), bytes(IID_Handler), bytes(IID_IAgileObject)):
            out[0] = this
            return 0
        out[0] = None
        return E_NOINTERFACE

    def _completed(self, _this, _operation) -> int:
        self.done.set()
        return 0


def _activate(pid: int) -> c_void_p:
    """Un IAudioClient que escucha solo al proceso `pid` (y a los que abra)."""
    mmdevapi = ctypes.WinDLL("Mmdevapi")
    activate = mmdevapi.ActivateAudioInterfaceAsync
    activate.argtypes = [wintypes.LPCWSTR, POINTER(GUID), POINTER(PROPVARIANT), c_void_p, POINTER(c_void_p)]
    activate.restype = HRESULT
    params = LoopbackParams(ACTIVATION_PROCESS_LOOPBACK, pid, INCLUDE_PROCESS_TREE)
    variant = PROPVARIANT(vt=VT_BLOB)
    variant.blob.cbSize = ctypes.sizeof(params)
    variant.blob.pBlobData = ctypes.addressof(params)
    handler = CompletionHandler()
    operation = c_void_p()
    activate("VAD\\Process_Loopback", byref(IID_IAudioClient), byref(variant), ctypes.addressof(handler.obj),
             byref(operation))
    try:
        if not handler.done.wait(5):
            raise TimeoutError("Windows no activó la captura del proceso")
        result, client = HRESULT(), c_void_p()
        _call(operation, 3, byref(result), byref(client), argtypes=(POINTER(HRESULT), POINTER(c_void_p)))
        if not client:
            raise OSError(f"No se pudo escuchar solo a Roblox ({result.value & 0xFFFFFFFF:#010x})")
        return client
    finally:
        _release(operation)


class ProcessLoopback:
    """Fuente de audio (como las de soundcard: `recorder()` y `record()`) con solo el sonido de un proceso."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.rate = 16000
        self._client: c_void_p | None = None
        self._capture: c_void_p | None = None
        self._event = None
        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._cond = threading.Condition()
        self._chunks: list[np.ndarray] = []
        self._available = 0
        self._t0 = 0.0
        self._given = 0
        self.error = ""

    # ------------------------------------------------------------ interfaz de soundcard
    def recorder(self, samplerate: int = 16000, channels: int = 1, blocksize: int | None = None) -> ProcessLoopback:
        self.rate = samplerate
        return self

    def __enter__(self) -> ProcessLoopback:
        self.start()
        return self

    def __exit__(self, *_exc) -> bool:
        self.stop()
        return False

    def record(self, numframes: int) -> np.ndarray:
        """`numframes` muestras mono, al ritmo real. Lo que Roblox no mandó (no sonaba nada) es silencio."""
        if not self._t0:
            self._t0 = time.perf_counter()
        due = self._t0 + (self._given + numframes) / self.rate
        with self._cond:
            while self._available < numframes and self._running.is_set():
                left = due + 0.08 - time.perf_counter()
                if left <= 0:
                    break
                self._cond.wait(left)
            if self.error and not self._running.is_set():
                raise OSError(self.error)
            data = np.concatenate(self._chunks) if self._chunks else np.zeros(0, np.float32)
            out, rest = data[:numframes], data[numframes:]
            self._chunks, self._available = ([rest] if len(rest) else []), len(rest)
        self._given += numframes
        if time.perf_counter() - due > 1.0:
            self._t0 = time.perf_counter() - self._given / self.rate  # la PC se frenó: se retoma sin atrasarse
        if len(out) < numframes:
            out = np.concatenate([out, np.zeros(numframes - len(out), np.float32)])
        return out

    # ------------------------------------------------------------ WASAPI
    def start(self) -> None:
        from .audio import com_ready

        com_ready()
        self._client = _activate(self.pid)
        fmt = WAVEFORMATEX(WAVE_FORMAT_IEEE_FLOAT, 1, self.rate, self.rate * 4, 4, 32, 0)
        _call(self._client, 3, 0, LOOPBACK | EVENTCALLBACK | AUTOCONVERTPCM | SRC_DEFAULT_QUALITY, 2_000_000, 0,
              byref(fmt), None, argtypes=(ctypes.c_int, wintypes.DWORD, ctypes.c_longlong, ctypes.c_longlong,
                                          POINTER(WAVEFORMATEX), c_void_p))
        capture = c_void_p()
        _call(self._client, 14, byref(IID_IAudioCaptureClient), byref(capture),
              argtypes=(POINTER(GUID), POINTER(c_void_p)))
        self._capture = capture
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateEventW.restype = wintypes.HANDLE
        self._event = kernel32.CreateEventW(None, False, False, None)
        _call(self._client, 13, wintypes.HANDLE(self._event), argtypes=(wintypes.HANDLE,))
        _call(self._client, 10)  # Start
        self._running.set()
        self._thread = threading.Thread(target=self._pump, name="bubble-audio-roblox", daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        kernel32 = ctypes.windll.kernel32
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        size = ctypes.c_uint32()
        data, frames, flags = c_void_p(), ctypes.c_uint32(), wintypes.DWORD()
        try:
            while self._running.is_set():
                kernel32.WaitForSingleObject(self._event, 100)
                while True:
                    _call(self._capture, 5, byref(size), argtypes=(POINTER(ctypes.c_uint32),))
                    if not size.value:
                        break
                    _call(self._capture, 3, byref(data), byref(frames), byref(flags), None, None,
                          argtypes=(POINTER(c_void_p), POINTER(ctypes.c_uint32), POINTER(wintypes.DWORD), c_void_p,
                                    c_void_p))
                    count = frames.value
                    if flags.value & BUFFER_SILENT or not data:
                        block = np.zeros(count, np.float32)
                    else:
                        block = np.ctypeslib.as_array(ctypes.cast(data, POINTER(ctypes.c_float)), (count,)).copy()
                    _call(self._capture, 4, count, argtypes=(ctypes.c_uint32,))
                    with self._cond:
                        self._chunks.append(block)
                        self._available += count
                        if self._available > 2 * self.rate:  # más de 2 s atrasado: lo viejo se descarta
                            data_all = np.concatenate(self._chunks)[-self.rate:]
                            self._chunks, self._available = [data_all], len(data_all)
                        self._cond.notify_all()
        except OSError as exc:  # Roblox se cerró, se desconectó el parlante...
            log.info("Se cortó el audio de Roblox: %s", exc)
            self.error = str(exc)
        finally:
            self._running.clear()
            with self._cond:
                self._cond.notify_all()

    def stop(self) -> None:
        self._running.clear()
        if self._thread:
            self._thread.join(1)
        if self._client:
            try:
                _call(self._client, 11)  # Stop
            except OSError:
                pass
        _release(self._capture)
        _release(self._client)
        self._capture = self._client = None
        if self._event:
            ctypes.windll.kernel32.CloseHandle(wintypes.HANDLE(self._event))
            self._event = None
