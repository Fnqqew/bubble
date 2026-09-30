"""Pruebas de la página «Pruebas»: micrófono, equipo y tiempos de cada paso de la voz.

- Micrófono: el jugador lee una frase y se mide el volumen de su voz, el ruido de fondo, la saturación y la proporción
  de la frase que Whisper entendió. Con eso se indica si el micrófono es adecuado para traducir (bien, normal o mal) y
  qué conviene cambiar.
- Equipo: procesador, memoria y placa de video, y el tiempo real que tarda este equipo en reconocer una frase y generar
  la voz. Con eso se estima el tiempo que transcurre entre el fin de la frase hablada y la voz traducida.
"""

from __future__ import annotations

import ctypes
import os
import re
import time
from dataclasses import dataclass, field

import numpy as np

SAMPLE_RATE = 16000
BLOCK = 512  # 32 ms: bloque que el detector de voz procesa por vez

# Frase que se lee en la prueba de micrófono (incluye pregunta, voseo y vocabulario de juego, como en una partida).
SENTENCES = {
    "es": "Hola, ¿alguien viene conmigo a la torre? Esperame, que ya voy.",
    "en": "Hey, is anyone coming with me to the tower? Wait for me, I'm on my way.",
    "pt": "Mano, alguém vem comigo até a torre? Espera aí que eu já vou.",
    "fr": "Salut, quelqu'un vient avec moi à la tour ? Attends-moi, j'arrive.",
    "de": "Hey, kommt jemand mit mir zum Turm? Warte auf mich, ich komme gleich.",
    "it": "Ehi, qualcuno viene con me alla torre? Aspettami, sto arrivando.",
    "nl": "Hé, gaat er iemand met me mee naar de toren? Wacht op me, ik kom eraan.",
    "ru": "Эй, кто-нибудь пойдёт со мной к башне? Подождите меня, я уже иду.",
    "uk": "Гей, хтось піде зі мною до вежі? Зачекайте мене, я вже йду.",
    "pl": "Hej, ktoś idzie ze mną do wieży? Poczekajcie na mnie, już idę.",
    "tr": "Hey, benimle kuleye gelen var mı? Beni bekleyin, geliyorum.",
    "id": "Hei, ada yang mau ikut aku ke menara? Tunggu aku, aku segera datang.",
    "vi": "Này, có ai đi cùng mình tới tòa tháp không? Đợi mình nhé, mình đến ngay.",
    "ko": "야, 누구 나랑 탑에 같이 갈 사람? 기다려, 금방 갈게.",
    "hi": "अरे, कोई मेरे साथ टावर चलेगा? मेरा इंतज़ार करो, मैं आ रहा हूँ।",
    "ar": "مرحبًا، هل يأتي أحد معي إلى البرج؟ انتظروني، أنا قادم.",
}  # (japonés, chino y tailandés no separan palabras con espacios: se usa la frase en inglés)
RATINGS = ("mal", "normal", "bien")


def sentence_for(language: str) -> str:
    return SENTENCES.get(language.split("-")[0].lower(), SENTENCES["en"])


def words(text: str) -> list[str]:
    """Palabras del texto, sin mayúsculas ni tildes: "Sí" y "si" se consideran iguales (no afecta la traducción)."""
    import unicodedata

    folded = unicodedata.normalize("NFKD", text.casefold())
    return re.findall(r"\w+", "".join(c for c in folded if not unicodedata.combining(c)))


def word_error_rate(reference: str, heard: str) -> float:
    """Proporción de palabras mal reconocidas, de 0 (ninguna) a 1 (todas)."""
    ref, hyp = words(reference), words(heard)
    row = list(range(len(hyp) + 1))
    for i, expected in enumerate(ref, 1):
        previous, row[0] = row[0], i
        for j, got in enumerate(hyp, 1):
            previous, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, previous + (expected != got))
    return min(1.0, row[len(hyp)] / max(1, len(ref)))


def record_phrase(mic, max_s: float = 10.0, quiet_s: float = 1.0, wait_s: float = 5.0, vad=None) -> np.ndarray:
    """Graba hasta que el jugador deja de hablar o se alcanza `max_s`. Si no habla en `wait_s`, la grabación termina
    igualmente.
    """
    from . import audio as audio_io

    if vad is None:
        from .vad import StreamingVad

        vad = StreamingVad()
    blocks: list[np.ndarray] = []
    audio_io.com_ready()
    with mic.recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=BLOCK * 4) as recorder:
        started = time.monotonic()
        spoke, quiet_since = False, None
        while time.monotonic() - started < max_s:
            block = audio_io.to_mono(recorder.record(numframes=BLOCK))
            blocks.append(block)
            probs = vad.feed(block)
            now = time.monotonic()
            if len(probs) and float(np.max(probs)) >= 0.5:
                spoke, quiet_since = True, None
            elif len(probs) and spoke:
                quiet_since = quiet_since or now
                if now - quiet_since >= quiet_s:
                    break
            if not spoke and now - started >= wait_s:
                break
    return np.concatenate(blocks) if blocks else np.zeros(0, np.float32)


@dataclass
class MicReport:
    rating: str  # "bien" | "normal" | "mal"
    voice_db: float  # volumen de la voz (dBFS: 0 es el máximo)
    noise_db: float  # ruido de fondo sin hablar
    clipped: float  # proporción de audio saturado
    accuracy: float  # palabras bien reconocidas (0 a 1)
    heard: str
    tips: list[str] = field(default_factory=list)

    @property
    def snr_db(self) -> float:
        return self.voice_db - self.noise_db


def _worst(*ratings: str) -> str:
    return min(ratings, key=RATINGS.index)


def analyze_mic(audio: np.ndarray, heard: str, expected: str, speech_probs: np.ndarray | None = None) -> MicReport:
    """Evalúa qué tan adecuado es el micrófono para traducir la voz. `speech_probs`: salida del detector de voz cada 32
    ms (si falta, se separa voz de silencio por volumen).
    """
    audio = np.asarray(audio, dtype=np.float32).ravel()
    frames = audio[: len(audio) // BLOCK * BLOCK].reshape(-1, BLOCK)
    if not len(frames):
        return MicReport("mal", -120.0, -120.0, 0.0, 0.0, heard, [("No me llega nada del micrófono. Verificá que "
                                                                   "esté conectado y elegido en la página Voz.")])
    rms = np.sqrt(np.mean(frames ** 2, axis=1)) + 1e-9
    if speech_probs is not None and len(speech_probs) >= len(rms):
        speech = np.asarray(speech_probs[:len(rms)]) >= 0.5
        silence = np.asarray(speech_probs[:len(rms)]) < 0.2
    else:
        order = np.sort(rms)
        speech = rms >= order[int(len(order) * 0.6)]
        silence = rms <= order[max(0, int(len(order) * 0.2) - 1)]
    voice_db = float(20 * np.log10(np.sqrt(np.mean(rms[speech] ** 2)))) if speech.any() else -120.0
    noise_db = float(20 * np.log10(np.median(rms[silence]))) if silence.any() else float(20 * np.log10(rms.min()))
    clipped = float(np.mean(np.abs(audio) > 0.98))
    accuracy = 1.0 - word_error_rate(expected, heard) if heard else 0.0

    tips: list[str] = []
    if voice_db < -42:
        level = "mal"
        tips.append("Te escucho muy bajo. Subí el volumen del micrófono en la configuración de sonido de "
                    "Windows, o acercátelo.")
    elif voice_db < -32:
        level = "normal"
        tips.append("Te escucho un poco bajo. Subí un poco el volumen o acercate el micrófono.")
    else:
        level = "bien"
    snr = voice_db - noise_db
    if snr < 12:
        noise = "mal"
        tips.append("Hay mucho ruido de fondo. Alejá el micrófono de ventiladores y parlantes, o usá "
                    "auriculares con micrófono.")
    elif snr < 20:
        noise = "normal"
        tips.append("Hay algo de ruido de fondo. Con auriculares con micrófono te entiendo mejor.")
    else:
        noise = "bien"
    if clipped > 0.001:
        clip = "normal"
        tips.append("Tu micrófono llega demasiado fuerte y se distorsiona. Bajale un poco el volumen o alejalo.")
    else:
        clip = "bien"
    if accuracy < 0.6:
        understood = "mal"
        if level == noise == "bien":
            tips.append("Te escucho bien, pero entendí pocas palabras. Probá hablar un poco más despacio. En "
                        "«Tu voz traducida» podés corregirme y aprendo tus palabras.")
    elif accuracy < 0.85:
        understood = "normal"
        tips.append("Te entendí casi todo. Cuanto más lo uses, mejor te voy a entender.")
    else:
        understood = "bien"
    return MicReport(_worst(level, noise, clip, understood), voice_db, noise_db, clipped, accuracy, heard, tips)


# ---------------------------------------------------------------- tu PC
def cpu_name() -> str:
    from ..performance import _registry

    return _registry(r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "ProcessorNameString") or "Procesador"


def gpu_names() -> list[str]:
    from ..performance import _gpus_from_registry

    return [name for name in _gpus_from_registry() if "basic" not in name.lower() and "virtual" not in name.lower()]


def memory_gb() -> float:
    class Status(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                    ("free", ctypes.c_ulonglong), ("total_page", ctypes.c_ulonglong),
                    ("free_page", ctypes.c_ulonglong), ("total_virtual", ctypes.c_ulonglong),
                    ("free_virtual", ctypes.c_ulonglong), ("free_extended", ctypes.c_ulonglong)]

    status = Status()
    status.length = ctypes.sizeof(Status)
    try:
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    except (AttributeError, OSError):
        return 0.0
    return status.total / 1024 ** 3


@dataclass
class PcReport:
    cpu: str
    threads: int
    memory_gb: float
    gpus: list[str]
    whisper: str  # modelo de reconocimiento de voz usado en esta PC
    understand_s: float  # reconocer una frase de ~3 s
    voice_s: float  # generar la voz traducida
    translate_s: float  # Claude (valor medido o típico)
    rating: str = ""
    expected_s: float = 0.0  # desde que termina de hablar hasta que suena la voz
    tips: list[str] = field(default_factory=list)


PAUSE_S = 0.3  # pausa necesaria para dar la frase por terminada


def rate_pc(report: PcReport) -> PcReport:
    report.expected_s = PAUSE_S + report.understand_s + report.translate_s + report.voice_s
    if report.expected_s < 2.6:
        report.rating = "excelente"
    elif report.expected_s < 3.6:
        report.rating = "bien"
    elif report.expected_s < 5.0:
        report.rating = "normal"
    else:
        report.rating = "lenta"
    tips = report.tips
    if report.threads < 6:
        tips.append("Tu procesador es algo justo, así que uso lo más liviano para no restarle fluidez a Roblox. En "
                    "Ajustes, en Rendimiento, podés elegir «Liviano».")
    if report.understand_s > 1.5:
        tips.append("En esta PC me cuesta entender tu voz rápido. Cerrá los programas pesados mientras jugás, "
                    "o probá Bubble Pro.")
    if report.memory_gb and report.memory_gb < 8:
        tips.append("Tenés poca memoria (menos de 8 GB). Con Roblox, Bubble y el navegador abiertos puede ir "
                    "lento, así que cerrá lo que no uses.")
    if not report.gpus:
        tips.append("No encontré placa de video, así que leo la pantalla con el procesador. Usa un poco más.")
    if report.translate_s > 2.5:
        tips.append("Claude tardó más de lo normal. Puede ser tu internet o que esté cargado.")
    if not tips:
        tips.append("Tu PC rinde de sobra con Bubble.")
    return report


def cpu_threads() -> int:
    return os.cpu_count() or 4
