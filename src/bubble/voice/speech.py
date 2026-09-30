"""Cómo se dijo algo, no solo qué: si la frase quedó incompleta, si fue pregunta o exclamación, y qué ejemplos darle a
Whisper para que transcriba con el estilo del hablante.

- Pausas: una pausa corta no siempre marca el final ("fui a la tienda y… compré"). Si lo último dicho suena a frase sin
  terminar (acaba en "y", "que", "porque", "el"…) se espera más audio; si suena terminada, se continúa de inmediato.
- Preguntas, exclamaciones y gritos: en español "¿vamos a la torre?" y "vamos a la torre" tienen las mismas palabras; lo
  que cambia es la entonación (la voz sube al final). Un grito se detecta por el volumen y el esfuerzo de la voz (al
  gritar se refuerzan los agudos, aunque el micrófono sature). Whisper no marca nada de esto, así que se mide en el
  audio, comparado con el habla habitual de esa persona, y se informa a Claude (la voz sintética lo acompaña).
- Ejemplos para Whisper: texto en ese idioma escrito como habla el jugador (voseo, jerga de juego, con ¿? y ¡!). Whisper
  lo toma como contexto previo y escribe de forma similar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

SAMPLE_RATE = 16000

# Palabras con las que una frase no suele terminar: si lo último dicho es una de ellas, la frase continúa.
CONTINUES = {
    "es": {"y", "e", "o", "u", "ni", "que", "pero", "porque", "pues", "entonces", "si", "cuando", "como", "donde",
           "de", "del", "a", "al", "con", "sin", "en", "por", "para", "hasta", "desde", "sobre", "el", "la", "los",
           "las", "un", "una", "unos", "unas", "mi", "mis", "tu", "tus", "su", "sus", "me", "te", "se", "le", "les",
           "nos", "muy", "más", "tan", "este", "esta", "ese", "esa", "eh", "em", "emm", "mmm", "osea", "tipo", "bueno"},
    "en": {"and", "or", "but", "so", "because", "cause", "if", "when", "then", "to", "of", "in", "on", "at", "for",
           "with", "from", "the", "a", "an", "my", "your", "his", "her", "our", "their", "i", "you", "we", "they",
           "is", "are", "was", "were", "am", "be", "gonna", "wanna", "gotta", "very", "really", "just", "like", "um",
           "uh", "uhm", "hmm", "that", "this", "which", "who"},
    "pt": {"e", "ou", "mas", "que", "porque", "então", "se", "quando", "como", "de", "do", "da", "dos", "das", "a",
           "o", "os", "as", "um", "uma", "no", "na", "em", "com", "sem", "pra", "para", "por", "meu", "minha", "seu",
           "sua", "tipo", "né", "é", "tá", "eh", "hum"},
}
_SENTENCE_END = re.compile(r"[.!?…。！？]['\"»”)]*$")
_WORDS = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?", re.UNICODE)

# Ejemplos del habla del jugador para Whisper, con signos de pregunta y exclamación (que el modelo reproduce).
EXAMPLES = {
    "es": "¿Vamos a la torre? ¡Dale, esperame! Che, ¿querés tradear? No, pará, ese es mi ítem. ¿Hacemos PvP? Tengo "
          "lag. ¿Cuántos robux tenés? ¿Alguien viene conmigo a farmear?",
    "en": "Wait, are you coming? Let's go fight the boss! Bro, do you wanna trade? No way, that's my item. Wanna PvP? "
          "I'm lagging. How many robux do you have?",
    "pt": "Mano, bora pro boss? Espera aí! Quer trocar? Não, esse item é meu. Quantos robux você tem? Alguém vem "
          "comigo?",
    "fr": "Tu viens avec moi ? Allez, on y va ! Tu veux échanger ? Non, c'est mon objet.",
    "de": "Kommst du mit? Los, gehen wir! Willst du tauschen? Nein, das ist mein Item.",
    "it": "Vieni con me? Dai, andiamo! Vuoi scambiare? No, quello è il mio oggetto.",
}


def last_word(text: str) -> str:
    words = _WORDS.findall(text.casefold())
    return words[-1] if words else ""


def sounds_unfinished(text: str, language: str) -> bool:
    """Lo último dicho no cierra una frase: termina en coma, en puntos suspensivos o en una palabra que siempre
    continúa ("y", "que", "el"…). Whisper suele agregar un punto final igualmente, por lo que se evalúa la palabra y
    no el signo.
    """
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.endswith((",", "...", "…", "-", ":", ";")):
        return True
    return last_word(stripped) in CONTINUES.get(language.split("-")[0].lower(), set())


def sounds_finished(text: str, language: str) -> bool:
    """Termina como una oración (. ? !) y no en una palabra que exige continuación."""
    return bool(_SENTENCE_END.search(text.strip())) and not sounds_unfinished(text, language)


QUESTION_RISE = 2.0  # semitonos (se usa el valor aprendido del jugador, si existe)
SHOUT_DB = 8.0  # dB por encima del volumen habitual
SHOUT_STRAIN = 5.0
SHOUT_EFFORT = -2.0  # sin referencia del hablante: voz con agudos muy marcados
EXCLAIM_DB = 5.0
SOFT_DB = -7.0
CLIPPED = 0.002


@dataclass(frozen=True)
class Melody:
    """Cómo sonó una frase: entonación, volumen y esfuerzo de la voz."""

    rise: float  # semitonos que sube el final respecto del resto (positivo = sube, como en una pregunta)
    pitch: float  # tono medio (Hz)
    level: float  # volumen medio de la voz (dB)
    effort: float  # agudos (1 a 4 kHz) contra graves (hasta 1 kHz), en dB: sube mucho al gritar
    spread: float  # semitonos entre el punto más grave y el más agudo: voz animada o monótona
    clipped: float  # proporción del audio saturado (gritos pegados al micrófono)
    voiced_s: float  # duración de la parte con voz

    def kind(self, usual: tuple[float, float, float] | None = None, question_rise: float = QUESTION_RISE,
             shout_db: float = SHOUT_DB, exclaim_db: float = EXCLAIM_DB) -> str:
        """Cómo se dijo: "question", "shout", "exclaim" o "soft", combinados con "+" ("question+shout"), o "".
        `usual`: (tono, volumen, esfuerzo) habituales de esa persona. Sin ese dato solo se detectan los gritos
        evidentes. Los umbrales pueden ser los de esa persona (ver VoiceProfile.calibrate).
        """
        if self.voiced_s < 0.3:
            return ""
        marks = ["question"] if self.rise >= question_rise else []
        if usual:
            pitch, level, effort = usual
            louder = self.level - level
            higher = self.pitch / pitch if pitch else 1.0
            strained = self.effort - effort
            if louder >= shout_db or (strained >= SHOUT_STRAIN and higher >= 1.2) or (
                    self.clipped > CLIPPED and louder >= 4):
                marks.append("shout")
            # más fuerte y más agudo: con solo más agudo había falsos positivos
            elif louder >= exclaim_db and higher >= 1.1:
                marks.append("exclaim")
            elif louder <= SOFT_DB and higher <= 1.02:
                marks.append("soft")
        elif self.effort >= SHOUT_EFFORT or self.clipped > 2 * CLIPPED:
            marks.append("shout")
        return "+".join(marks)


class Usual:
    """Cómo suele hablar una persona: promedio adaptativo (tono, volumen, esfuerzo)."""

    MIN = 3

    def __init__(self) -> None:
        self.pitch = self.level = self.effort = 0.0
        self.count = 0

    def add(self, tune: Melody | None) -> None:
        if tune is None or tune.voiced_s < 0.5:
            return
        weight = 1 / (self.count + 1) if self.count < 20 else 0.05
        if self.count == 0:
            self.pitch, self.level, self.effort = tune.pitch, tune.level, tune.effort
        else:
            self.pitch += (tune.pitch - self.pitch) * weight
            self.level += (tune.level - self.level) * weight
            self.effort += (tune.effort - self.effort) * weight
        self.count += 1

    def get(self) -> tuple[float, float, float] | None:
        return (self.pitch, self.level, self.effort) if self.count >= self.MIN else None


def melody(audio: np.ndarray, rate: int = SAMPLE_RATE) -> Melody | None:
    """Tono (por autocorrelación, cada 10 ms), volumen y esfuerzo de la voz. Devuelve None si hay muy poca voz."""
    audio = np.asarray(audio, dtype=np.float32).ravel()
    frame, hop = int(0.04 * rate), int(0.01 * rate)
    count = 1 + (len(audio) - frame) // hop
    if count < 20:
        return None
    index = np.arange(frame)[None, :] + hop * np.arange(count)[:, None]
    frames = audio[index]
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    loud = rms > max(float(rms.max()) * 0.15, 1e-3)
    frames = (frames - frames.mean(axis=1, keepdims=True)) * np.hanning(frame)
    spectrum = np.abs(np.fft.rfft(frames, n=2 * frame)) ** 2
    corr = np.fft.irfft(spectrum)[:, :frame]
    corr /= corr[:, :1] + 1e-9
    low, high = rate // 600, rate // 60  # voces de 60 a 600 Hz (un grito agudo supera los 400)
    window = corr[:, low:high]
    lag = window.argmax(axis=1) + low
    voiced = loud & (window.max(axis=1) > 0.5)
    if voiced.sum() < 12:
        return None
    pitch = rate / lag[voiced]
    semis = 12 * np.log2(pitch / np.median(pitch))
    keep = np.abs(semis) < 9  # errores de octava (el doble o la mitad del tono real)
    semis = semis[keep]
    if len(semis) < 12:
        return None
    tail = max(6, min(35, len(semis) // 4))  # el final: último cuarto, entre 60 y 350 ms de voz
    rise = float(np.median(semis[-tail:]) - np.median(semis[:-tail]))
    level = float(20 * np.log10(np.mean(rms[voiced]) + 1e-9))
    hz = rate / (2 * frame)  # ancho de cada banda del espectro
    grave = spectrum[voiced, int(80 / hz):int(1000 / hz)].sum(axis=1)
    agudo = spectrum[voiced, int(1000 / hz):int(4000 / hz)].sum(axis=1)
    effort = float(np.median(10 * np.log10((agudo + 1e-12) / (grave + 1e-12))))
    spread = float(np.percentile(semis, 90) - np.percentile(semis, 10))
    clipped = float(np.mean(np.abs(audio) > 0.98))
    return Melody(rise, float(np.median(pitch[keep])), level, effort, spread, clipped,
                  float(voiced.sum() * hop / rate))
