"""¿Hay alguien hablando? Silero VAD (viene con faster-whisper): cada 32 ms, la probabilidad de que sea voz.

Distingue la voz de la música del juego, pasos, explosiones o viento mucho mejor que medir el volumen.
"""

from __future__ import annotations

import threading

import numpy as np

FRAME = 512  # 32 ms a 16 kHz
CONTEXT = 64


def _model_path() -> str:
    from faster_whisper.utils import get_assets_path

    import os

    return os.path.join(get_assets_path(), "silero_vad_v6.onnx")


_shared: list = []
_lock = threading.Lock()


def session():
    """Un solo modelo para todas las escuchas (cada una guarda su propio estado): crearlo congela la ventana un
    momento (onnxruntime no suelta a Python mientras tanto), así que se crea una vez, al abrir Bubble."""
    with _lock:
        if not _shared:
            import onnxruntime

            options = onnxruntime.SessionOptions()
            options.intra_op_num_threads = 1
            options.inter_op_num_threads = 1
            options.log_severity_level = 3
            _shared.append(onnxruntime.InferenceSession(_model_path(), options, providers=["CPUExecutionProvider"]))
        return _shared[0]


class StreamingVad:
    def __init__(self) -> None:
        self._session = session()
        self.reset()

    def reset(self) -> None:
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._context = np.zeros(CONTEXT, dtype=np.float32)
        self._pending = np.zeros(0, dtype=np.float32)

    def feed(self, samples: np.ndarray) -> np.ndarray:
        """Audio nuevo (mono, 16 kHz) → probabilidades de voz de cada cuadro completo de 32 ms."""
        data = np.concatenate([self._pending, np.asarray(samples, dtype=np.float32).ravel()])
        count = len(data) // FRAME
        self._pending = data[count * FRAME:]
        if not count:
            return np.zeros(0, dtype=np.float32)
        frames = data[: count * FRAME].reshape(count, FRAME)
        contexts = np.vstack([self._context[None], frames[:-1, -CONTEXT:]])
        self._context = frames[-1, -CONTEXT:].copy()
        batch = np.concatenate([contexts, frames], axis=1)
        probs, self._h, self._c = self._session.run(None, {"input": batch, "h": self._h, "c": self._c})
        return np.asarray(probs, dtype=np.float32).ravel()
