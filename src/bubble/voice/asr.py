"""Voz a texto casi instantánea: Whisper mirando solo el audio que hay.

Whisper se entrenó con ventanas de 30 s y, usado de la forma normal, procesa siempre 30 s aunque la frase dure 2
(en un procesador de 6 núcleos: ~1,7 s con "base", ~5,4 s con "small"). Acá se le pasa solo la frase más 1 s de
silencio: la parte pesada tarda 40 veces menos ("small" transcribe en ~0,5 s y "base" en ~0,15 s). Sin la ventana
completa el modelo a veces no sabe dónde terminar y repite la frase: eso se corta (ver `cut_repetitions`).

Medido con el laboratorio de voz (tools/voice_lab.py): con "small" así se equivoca menos que "base" de la forma normal.
"""

from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .models import models_dir
from .stt import is_hallucination

SAMPLE_RATE = 16000
PAD_S = 1.0  # silencio al final: le avisa al modelo que la frase terminó
TOKENS_PER_S = 8  # tope de largo del texto según lo que dura el audio (evita que siga inventando)
# Las escrituras no latinas ocupan muchos más tokens por palabra (el hindi, hasta 4 por letra).
DENSE_SCRIPTS = {"hi", "bn", "ta", "te", "mr", "gu", "th", "ar", "fa", "ur", "he", "el", "zh", "ja", "ko", "ru", "uk",
                 "ka", "hy", "km", "my", "si", "ne"}
DENSE_TOKENS_PER_S = 30
# Idiomas que se escuchan en Roblox. Con ruido o poco audio Whisper a veces "detecta" idiomas raros (galés, latín,
# hawaiano…): esos casi no se consideran.
LIKELY = {"en", "es", "pt", "fr", "de", "it", "nl", "ru", "uk", "pl", "tr", "ar", "hi", "ur", "bn", "id", "ms", "vi",
          "th", "tl", "zh", "ja", "ko", "sv", "no", "da", "fi", "ro", "hu", "cs", "el", "he", "fa", "ta", "te"}
UNLIKELY_WEIGHT = 0.05
MAX_HINT_TOKENS = 160  # alcanza para tus palabras y tus frases; más largo demora (medido)


@dataclass
class Heard:
    text: str
    language: str  # "en", "pt"…
    language_prob: float
    no_speech: float  # probabilidad de que no haya nadie hablando
    logprob: float  # confianza promedio del texto
    seconds: float  # duración del audio
    took: float  # lo que tardó


def cut_repetitions(text: str) -> str:
    """Corta cuando el modelo vuelve a empezar la frase ("hola che, hola che, hola…") o se traba repitiendo lo mismo."""
    words = text.split()
    plain = [re.sub(r"[^\w']", "", word.lower()) for word in words]
    # Vuelve a empezar desde el principio.
    head = plain[:3]
    if len(head) == 3 and all(head):
        for index in range(3, len(plain) - 2):
            if plain[index:index + 3] == head:
                words, plain = words[:index], plain[:index]
                break
    # Termina volviendo a empezar la primera oración ("…comigo? Galera"): se saca ese final.
    ends = [index for index, word in enumerate(words[:-1]) if re.search(r"[.!?]$", word)]
    if ends:
        last = plain[ends[-1] + 1:]
        if last and last == plain[:len(last)] and len(last) < len(plain) // 2:
            words, plain = words[:ends[-1] + 1], plain[:ends[-1] + 1]
    # Se traba en un bucle: el mismo grupo de 1 a 4 palabras varias veces seguidas.
    for size in (1, 2, 3, 4):
        repeats = 4 if size == 1 else 3
        index = 0
        while index + size * repeats <= len(plain):
            chunk = plain[index:index + size]
            if all(plain[index + size * k:index + size * (k + 1)] == chunk for k in range(1, repeats)):
                # Se deja una vuelta, alineada con cómo termina el texto ("find it for it", no "find it for").
                shift = (len(plain) - index) % size if size > 1 else 0
                keep = index + shift + size * (repeats - 1 if size == 1 else 1)
                words, plain = words[:keep], plain[:keep]
                break
            index += 1
    return " ".join(words).strip(" ,")


def _pick_language(scores: list[tuple[str, float]], prior: dict[str, float]) -> tuple[str, float]:
    best, best_score, best_prob = "en", -1.0, 0.0
    for token, probability in scores:
        language = token[2:-2]
        weight = prior.get(language, 1.0 if language in LIKELY else UNLIKELY_WEIGHT)
        if probability * weight > best_score:
            best, best_score, best_prob = language, probability * weight, probability
    return best, best_prob


UNSURE_LOGPROB = -0.5


def _unsure(result) -> bool:
    return not result.scores or float(result.scores[0]) < UNSURE_LOGPROB or result.no_speech_prob > 0.5


def pick_models(threads: int | None = None) -> tuple[str, str]:
    """(modelo para ir mostrando el texto mientras hablan, modelo para la versión final), según el procesador."""
    threads = threads or os.cpu_count() or 4
    if threads >= 10:
        return "base", "small"
    if threads >= 6:
        return "tiny", "base"
    return "", "base"  # procesadores chicos: solo el texto final


class FastWhisper:
    def __init__(self, name: str, threads: int | None = None) -> None:
        from faster_whisper import WhisperModel

        threads = threads or max(2, min(6, (os.cpu_count() or 4) // 2))
        self.name = name
        self._model = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=threads,
                                   download_root=str(models_dir() / "whisper"))
        self._tokenizers: dict[str, object] = {}
        self._lock = threading.Lock()

    def _tokenizer(self, language: str):
        from faster_whisper.tokenizer import Tokenizer

        if language not in self._tokenizers:
            self._tokenizers[language] = Tokenizer(self._model.hf_tokenizer, True, task="transcribe",
                                                   language=language)
        return self._tokenizers[language]

    def transcribe(self, audio: np.ndarray, language: str | None = None, beam_size: int = 1,
                   prior: dict[str, float] | None = None, retry_beam: int = 0,
                   hint: str | Callable[[str], str] = "") -> Heard | None:
        """`audio`: mono, float32, 16 kHz. `language`: si ya se sabe, no se detecta. `prior`: peso extra de algunos
        idiomas al detectarlo (los que se vienen escuchando). `hint`: texto de ejemplo en ese idioma (o una función
        idioma → texto): Whisper escribe parecido (tus palabras, el voseo, los ¿? y ¡!). None si no había voz."""
        import ctranslate2

        started = time.perf_counter()
        language = language.split("-")[0].lower() if language else None  # "es-AR" → "es"
        seconds = len(audio) / SAMPLE_RATE
        padded = np.concatenate([np.asarray(audio, dtype=np.float32), np.zeros(int(PAD_S * SAMPLE_RATE), np.float32)])
        with self._lock:
            features = self._model.feature_extractor(padded)
            frames = features.shape[1] - features.shape[1] % 2  # el codificador pide una cantidad par
            view = ctranslate2.StorageView.from_array(np.ascontiguousarray(features[None, :, :frames], np.float32))
            encoded = self._model.model.encode(view, to_cpu=False)
            probability = 1.0
            if not language:
                language, probability = _pick_language(self._model.model.detect_language(encoded)[0], prior or {})
            tokenizer = self._tokenizer(language)
            prompt = [tokenizer.sot, tokenizer.language, tokenizer.transcribe, tokenizer.no_timestamps]
            example = hint(language) if callable(hint) else hint
            if example:
                # "Lo que se dijo antes" (así lo usa Whisper): marca el estilo del texto, no se transcribe.
                prompt = [tokenizer.sot_prev, *tokenizer.encode(" " + example.strip())[-MAX_HINT_TOKENS:], *prompt]
            limit = min(440, int((DENSE_TOKENS_PER_S if language in DENSE_SCRIPTS else TOKENS_PER_S) * seconds) + 12)
            limit = min(448, limit + len(prompt))  # el tope cuenta también las pistas

            def decode(beams: int):
                return self._model.model.generate(
                    encoded, [prompt], beam_size=beams, max_length=limit, suppress_blank=True,
                    repetition_penalty=1.1, return_scores=True, return_no_speech_prob=True,
                )[0]

            result = decode(beam_size)
            if retry_beam > beam_size and _unsure(result):
                # Salió dudosa (voces rápidas, gritos, música del juego): se prueba con varias hipótesis, reusando lo
                # ya calculado. La voz clara no paga ese costo (el doble de procesador y de demora, medido).
                # En frases largas, menos hipótesis: con 5 el final de un monólogo tardaba 2 s (medido).
                result = decode(retry_beam if seconds <= 5 else min(retry_beam, 3))
        tokens = result.sequences_ids[0]
        text = cut_repetitions(tokenizer.decode(tokens).strip())
        logprob = float(result.scores[0]) if result.scores else 0.0
        heard = Heard(text, language, float(probability), float(result.no_speech_prob), logprob, seconds,
                      time.perf_counter() - started)
        # Solo se descarta con evidencia fuerte de que no había voz: el detector de voz ya dijo que alguien hablaba, y
        # con la música y los efectos del juego Whisper duda más (se tiraban frases enteras de verdad).
        if not text or is_hallucination(text) or (heard.no_speech > 0.8 and logprob < -1.0):
            return None
        return heard
