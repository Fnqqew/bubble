"""Las voces que trae Windows (las de «leer en voz alta»), para cuando Windows no deja usar las de Piper.

Windows 11 con el «Control inteligente de aplicaciones» (Smart App Control) activado bloquea los programas sin firma
digital, y una parte de Piper (espeakbridge) no la tiene: Bubble se quedaba sin voz sintética en Basic («DLL load
failed… Una directiva de Control de aplicaciones bloqueó este archivo»). Las voces de Windows son de Microsoft
(firmadas): siempre andan. Suenan un poco menos naturales que las de Piper.

Se usan las más nuevas («OneCore»: Sabina, Raúl, Zira, Mark…) si están; si no, las de siempre (SAPI). Cada idioma que
agregues en Windows (Configuración › Hora e idioma › Voz) suma sus voces.
"""

from __future__ import annotations

import ctypes
import threading
from dataclasses import dataclass

import numpy as np

CATEGORIES = (r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices",  # las nuevas (más naturales)
              r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech\Voices")
SAMPLE_RATE = 22050
FORMAT_22K_16BIT_MONO = 22  # SAFT22kHz16BitMono
# Cómo acompaña la voz lo que sentiste (como en tts.STYLES): (velocidad, volumen 0..100).
STYLES = {"": (1.0, 100), "shout": (1.12, 100), "exclaim": (1.06, 100), "soft": (0.94, 70)}
# Sin la región exacta, la más cercana a la mayoría de los jugadores (como con Piper): español latino, no de España.
PREFERRED_REGION = {"es": "es-mx", "en": "en-us", "pt": "pt-br", "fr": "fr-fr", "de": "de-de", "zh": "zh-cn"}


@dataclass(frozen=True)
class WindowsVoice:
    name: str  # "Microsoft Sabina"
    language: str  # "es-MX"
    gender: str  # "femenina" o "masculina"
    category: str
    token_id: str


def _locale(lcid_hex: str) -> str:
    """"80A" → "es-MX" (el idioma de una voz, como lo guarda Windows)."""
    try:
        lcid = int(lcid_hex.split(";")[0], 16)
    except ValueError:
        return ""
    buffer = ctypes.create_unicode_buffer(85)
    return buffer.value if ctypes.windll.kernel32.LCIDToLocaleName(lcid, buffer, 85, 0) else ""


def _com() -> None:
    ctypes.windll.ole32.CoInitializeEx(None, 0)  # cada hilo que usa las voces (si ya estaba, no hace nada)


def _apartment() -> int | None:
    """Cómo tiene preparado COM este hilo: 0 o 3 = «un solo hilo» (el de la ventana, Tk), 1 = «multihilo» (el audio);
    None si todavía no lo preparó nadie."""
    kind, qualifier = ctypes.c_int(), ctypes.c_int()
    if ctypes.windll.ole32.CoGetApartmentType(ctypes.byref(kind), ctypes.byref(qualifier)) != 0:
        return None
    return kind.value


def _comtypes():
    """comtypes, preparado igual que COM en este hilo. Al cargarse, comtypes prepara COM a su manera, y si el hilo ya
    estaba preparado distinto (la ventana usa «un solo hilo»; el audio, «multihilo»), Windows no deja («No se puede
    cambiar el modo de subproceso después de establecerlo»)."""
    import sys

    if "comtypes" not in sys.modules:
        sys.coinit_flags = 2 if _apartment() in (0, 3) else 0  # APARTMENTTHREADED o MULTITHREADED
    import comtypes.client

    return comtypes.client


class WindowsVoices:
    def __init__(self) -> None:
        self._voices: list[WindowsVoice] | None = None
        self._lock = threading.Lock()

    def voices(self) -> list[WindowsVoice]:
        if self._voices is None:
            client = _comtypes()
            _com()
            found: list[WindowsVoice] = []
            seen: set[tuple[str, str]] = set()
            for category in CATEGORIES:
                try:
                    tokens = client.CreateObject("SAPI.SpObjectTokenCategory")
                    tokens.SetId(category, False)
                    for token in tokens.EnumerateTokens():
                        name = token.GetAttribute("Name") or token.GetDescription()
                        language = _locale(token.GetAttribute("Language") or "")
                        if not language or (name, language) in seen:
                            continue  # (la misma voz en las dos listas: queda la nueva)
                        seen.add((name, language))
                        gender = "masculina" if (token.GetAttribute("Gender") or "").lower() == "male" else "femenina"
                        found.append(WindowsVoice(name, language, gender, category, token.Id))
                except (OSError, AttributeError):
                    continue
            self._voices = found
        return self._voices

    def voice_for(self, language: str, gender: str = "femenina") -> WindowsVoice | None:
        """La mejor voz de Windows para ese idioma: la misma región si hay, y el género pedido si hay."""
        family = language.split("-")[0].lower()
        candidates = [voice for voice in self.voices() if voice.language.split("-")[0].lower() == family]
        if not candidates:
            return None
        wanted = language.lower()
        preferred = PREFERRED_REGION.get(family, "")

        def rank(voice: WindowsVoice):
            region = voice.language.lower()
            return (voice.gender != gender, region != wanted, region != preferred, voice.category != CATEGORIES[0])

        return min(candidates, key=rank)

    def has(self, language: str) -> bool:
        return self.voice_for(language) is not None

    def synthesize(self, text: str, language: str, gender: str = "femenina", speed: float = 1.0,
                   style: str = "") -> tuple[np.ndarray, int] | None:
        """(audio float32 -1..1, frecuencia) o None si Windows no tiene voz para ese idioma."""
        voice = self.voice_for(language, gender)
        if voice is None or not text.strip():
            return None
        client = _comtypes()
        marks = set(style.split("+")) if style else set()
        mood = next((mark for mark in ("shout", "exclaim", "soft") if mark in marks), "")
        pace, volume = STYLES[mood]
        with self._lock:
            _com()
            tokens = client.CreateObject("SAPI.SpObjectTokenCategory")
            tokens.SetId(voice.category, False)
            token = next((t for t in tokens.EnumerateTokens() if t.Id == voice.token_id), None)
            if token is None:
                return None
            speaker = client.CreateObject("SAPI.SpVoice")
            stream = client.CreateObject("SAPI.SpMemoryStream")
            audio_format = stream.Format
            audio_format.Type = FORMAT_22K_16BIT_MONO
            stream.Format = audio_format
            speaker.Voice = token
            speaker.AudioOutputStream = stream
            # SAPI: -10 (lento) a 10 (rápido); 0 es la velocidad normal de la voz.
            speaker.Rate = max(-10, min(10, round((speed * pace - 1.0) * 10)))
            speaker.Volume = volume
            speaker.Speak(text, 0)
            data = bytes(stream.GetData())
        if not data:
            return None
        audio = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        return audio, SAMPLE_RATE
