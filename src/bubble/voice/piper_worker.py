"""Voces de Piper en un proceso aparte.

Cargar una voz tarda unos 2 s y, durante ese tiempo, onnxruntime mantiene el bloqueo global de Python (GIL): la ventana,
la barra de escritura y los subtítulos quedaban congelados (entre 1,6 y 1,9 s al abrir Bubble y al cambiar de idioma con
Tab, que prepara la voz del idioma nuevo). En un proceso separado, la carga y la síntesis no bloquean la interfaz.

La comunicación usa las tuberías del proceso (stdin/stdout): cada mensaje es su longitud (4 bytes) seguida de un JSON;
la respuesta a "say" incluye a continuación el audio (float32). Si el proceso no arranca o se cae, `Voices` sigue
funcionando en el proceso actual.
"""

from __future__ import annotations

import json
import logging
import os
import struct
import subprocess
import sys
import threading
from collections import OrderedDict
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MAX_LOADED = 3  # voces cargadas a la vez en el proceso (60-100 MB cada una)


class VoiceError(Exception):
    """La voz no pudo cargarse o sintetizar el texto (el proceso sigue activo)."""


def _python() -> str:
    """Usa python.exe (sin ventana) en lugar de pythonw.exe: con pythonw, las tuberías del proceso no siempre
    funcionan.
    """
    executable = Path(sys.executable)
    console = executable.with_name("python.exe")
    return str(console if executable.name.lower() == "pythonw.exe" and console.exists() else executable)


def _write(stream, message: dict, payload: bytes = b"") -> None:
    data = json.dumps(message).encode("utf-8")
    stream.write(struct.pack("<I", len(data)) + data + payload)
    stream.flush()


def _read_exactly(stream, size: int) -> bytes:
    parts, missing = [], size
    while missing:
        chunk = stream.read(missing)
        if not chunk:
            raise EOFError("se cerró el proceso de voces")
        parts.append(chunk)
        missing -= len(chunk)
    return b"".join(parts)


def _read(stream) -> dict:
    (size,) = struct.unpack("<I", _read_exactly(stream, 4))
    return json.loads(_read_exactly(stream, size))


class PiperProcess:
    """Proceso de voces visto desde Bubble. Atiende un pedido a la vez (cada voz sintetiza una frase por vez de todos
    modos).
    """

    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self.loaded: list[str] = []  # voces cargadas en el proceso, la más reciente al final
        self.failed = ""  # motivo por el que no se pudo usar el proceso (se usa el actual)

    def _start(self) -> subprocess.Popen:
        source = str(Path(__file__).resolve().parents[2])  # carpeta que contiene "bubble", aunque no esté instalado
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, [source, os.environ.get("PYTHONPATH")])),
                   PYTHONIOENCODING="utf-8")
        process = subprocess.Popen([_python(), "-m", "bubble.voice.piper_worker"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   creationflags=NO_WINDOW, env=env)
        reply = _read(process.stdout)  # el proceso avisa que está listo (o el motivo por el que no)
        if not reply.get("ok"):
            process.kill()
            raise RuntimeError(reply.get("error") or "el proceso de voces no arrancó")
        self.loaded = []
        return process

    def _ask(self, message: dict) -> tuple[dict, bytes]:
        with self._lock:
            for attempt in (0, 1):
                if self._process is None or self._process.poll() is not None:
                    self._process = self._start()
                try:
                    _write(self._process.stdin, message)
                    reply = _read(self._process.stdout)
                    payload = _read_exactly(self._process.stdout, 4 * reply["samples"]) if reply.get("samples") else b""
                    return reply, payload
                except (EOFError, OSError, ValueError, struct.error) as exc:
                    self.close()  # se cayó: se reinicia una sola vez
                    if attempt:
                        raise RuntimeError(f"el proceso de voces se cerró: {exc}") from exc
        raise RuntimeError("el proceso de voces no responde")

    def load(self, name: str, model: Path, config: Path) -> None:
        reply, _ = self._ask({"op": "load", "name": name, "model": str(model), "config": str(config)})
        if not reply.get("ok"):
            raise VoiceError(reply.get("error") or f"no se pudo cargar la voz {name}")
        self._remember(name)

    def say(self, name: str, model: Path, config: Path, text: str, settings: dict) -> tuple[np.ndarray, int] | None:
        reply, payload = self._ask({"op": "say", "name": name, "model": str(model), "config": str(config),
                                    "text": text, **settings})
        if not reply.get("ok"):
            raise VoiceError(reply.get("error") or f"la voz {name} no pudo decir el texto")
        self._remember(name)
        if not reply.get("samples"):
            return None
        return np.frombuffer(payload, dtype="<f4").copy(), int(reply["rate"])

    def _remember(self, name: str) -> None:
        if name in self.loaded:
            self.loaded.remove(name)
        self.loaded.append(name)
        del self.loaded[:-MAX_LOADED]

    def close(self) -> None:
        process, self._process = self._process, None
        self.loaded = []
        if process is not None:
            try:
                process.kill()
            except OSError:
                pass


# ---------------------------------------------------------------- el proceso de voces
def _serve() -> None:
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    sys.stdout = sys.stderr  # evita que cualquier impresión se mezcle con los mensajes
    try:
        from piper import PiperVoice
        from piper.config import SynthesisConfig
    except Exception as exc:  # noqa: BLE001 - se avisa y Bubble usa las voces en su proceso (o las de Windows)
        _write(stdout, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return
    _write(stdout, {"ok": True})
    voices: OrderedDict[str, object] = OrderedDict()

    def voice(message: dict):
        name = message["name"]
        if name not in voices:
            voices[name] = PiperVoice.load(message["model"], config_path=message["config"])
            while len(voices) > MAX_LOADED:
                voices.popitem(last=False)
        voices.move_to_end(name)
        return voices[name]

    while True:
        try:
            message = _read(stdin)
        except (EOFError, OSError, ValueError, struct.error):
            return  # Bubble se cerró
        try:
            chosen = voice(message)
            if message["op"] == "load":
                _write(stdout, {"ok": True})
                continue
            config = SynthesisConfig(speaker_id=message.get("speaker"), length_scale=message.get("length_scale"),
                                     noise_scale=message.get("noise_scale"), noise_w_scale=message.get("noise_w"))
            chunks = list(chosen.synthesize(message["text"], config))
            if not chunks:
                _write(stdout, {"ok": True, "samples": 0})
                continue
            audio = np.concatenate([chunk.audio_float_array for chunk in chunks]).astype("<f4")
            _write(stdout, {"ok": True, "samples": len(audio), "rate": chunks[0].sample_rate}, audio.tobytes())
        except Exception as exc:  # noqa: BLE001 - una voz que falla no corta el proceso
            _write(stdout, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    _serve()
