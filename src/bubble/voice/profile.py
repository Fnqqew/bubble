"""Lo que Bubble aprende de la forma de hablar del jugador para entenderlo mejor y traducir más rápido cuanto más se usa.

- Palabras (nombres, jerga, correcciones) y frases (solo las confirmadas por el jugador): se envían a Claude, que así
  interpreta lo que se quiso decir aunque Whisper haya escuchado otra cosa. No se envían a Whisper: con listas largas de
  palabras o frases de ejemplo, en frases cortas ("hola") inventaba o repetía ("Hola Hola Hola", "Podla"). Whisper
  recibe solo un ejemplo fijo y corto (voice/speech.py).
- Cuánto sube la voz al preguntar y cómo suena el grito (si se aprendió): cada persona pregunta y grita distinto.
- Estilo de traducción: las traducciones aprobadas o corregidas en Pruebas, que Claude usa de modelo.
- Frases ya traducidas: si el jugador repite lo mismo ("dale, esperame"), la traducción sale de inmediato, sin consultar
  a Claude.
- Voz habitual (tono y volumen): permite detectar cuándo el jugador exclama o grita.
- Duración de cada paso, para mostrarla en Pruebas.

Solo se aprende lo seguro: nada con palabras repetidas ni con secuencias que no son palabras ("yonna kiona giona"). Lo
aprendido así en versiones anteriores se limpia al abrir (ver `_clean`).

Se guarda en %LOCALAPPDATA%\\Bubble\\perfil_voz.json, solo en el equipo del jugador. Se borra desde la página Pruebas.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path

from .speech import EXAMPLES, Melody

log = logging.getLogger(__name__)
MAX_PHRASES = 40
MAX_EXAMPLES = 8
EXAMPLES_IN_PROMPT = 6
MAX_SAVED = 400
SAVE_UP_TO_WORDS = 8  # las frases más largas dependen del contexto: no se reutilizan
MIN_MELODIES = 5  # con menos frases no se conoce el habla habitual
MAX_WORDS = 120
WORDS_FOR_CLAUDE = 60
# Una pregunta debe subir el tono, y de forma apreciable: con 0,5 semitonos las afirmaciones que suben un poco al final,
# muy comunes en el habla rioplatense, se traducían como preguntas.
QUESTION_RISE_RANGE = (1.5, 4.0)
_WORDS = re.compile(r"\w+", re.UNICODE)


def default_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    return Path(base) / "Bubble" / "perfil_voz.json"


def phrase_key(text: str) -> str:
    """Mismas palabras = misma frase (sin mayúsculas ni signos), pero una pregunta no equivale a una afirmación, ni un
    grito a algo dicho con calma.
    """
    words = " ".join(_WORDS.findall(text.casefold()))
    return words + ("?" if "?" in text else "") + ("!" if "!" in text else "")


def looks_clean(text: str) -> bool:
    """Indica si la frase puede aprenderse. No si repite palabras seguidas ("Hola Hola Hola", "comandas, comandas") ni
    si casi ninguna es una palabra conocida ("yonna kiona giona giona"): eso es Whisper inventando, no habla real.
    """
    from ..translate.langdetect import is_gaming, known_anywhere

    words = [w.casefold() for w in _WORDS.findall(text)]
    if not words:
        return False
    if any(words[i] == words[i + 1] for i in range(len(words) - 1)):
        return False
    half = len(words) // 2
    if half >= 2 and words[:half] == words[half:2 * half]:
        return False  # "como andas como andas"
    # La lista de palabras comunes es pequeña ("torre" no figura): solo se rechaza si casi ninguna es conocida.
    unknown = [w for w in words if not (known_anywhere(w) or is_gaming(w) or w.isdigit())]
    return len(words) < 3 or len(unknown) < 0.8 * len(words)


def _common(word: str) -> bool:
    from ..translate.langdetect import known_anywhere

    return known_anywhere(word)


class VoiceProfile:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_path()
        self._lock = threading.Lock()
        self.data: dict = self._empty()
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.data.update({key: value for key, value in loaded.items() if isinstance(value, dict)})
        except (OSError, ValueError):
            pass
        if self._clean():
            self.save()

    def _clean(self) -> bool:
        """Elimina lo aprendido incorrectamente (versiones anteriores aprendían sin validar). Devuelve True si
        cambió algo.
        """
        before = json.dumps(self.data, sort_keys=True, ensure_ascii=False)
        self.data["phrases"] = {lang: [p for p in phrases if looks_clean(p)]
                                for lang, phrases in self.data["phrases"].items()}
        self.data["saved"] = {key: value for key, value in self.data["saved"].items()
                              if looks_clean(key.split("|", 1)[-1]) and looks_clean(value)}
        self.data["words"] = {lang: list(dict.fromkeys(w for w in words if len(w) > 2 and not _common(w)))
                              for lang, words in self.data["words"].items()}
        rise = self.data["calibration"].get("question_rise")
        if rise is not None:
            low, high = QUESTION_RISE_RANGE
            self.data["calibration"]["question_rise"] = min(high, max(low, rise))
        return json.dumps(self.data, sort_keys=True, ensure_ascii=False) != before

    @staticmethod
    def _empty() -> dict:
        return {"phrases": {}, "examples": {}, "saved": {}, "melody": {}, "times": {}, "words": {},
                "calibration": {}}

    # ------------------------------------------------------------ Whisper y Claude: tus palabras
    def hint(self, language: str) -> str:
        """Ejemplo para Whisper en ese idioma: fijo y corto, con rasgos del habla (voseo, jerga, ¿? ¡!). No incluye
        nada de lo aprendido: con eso Whisper inventaba en las frases cortas.
        """
        return EXAMPLES.get(language.split("-")[0].lower(), "")

    def vocabulary(self, language: str) -> tuple[str, ...]:
        """Palabras y nombres del jugador, para Claude: le permiten interpretar lo que se quiso decir si Whisper
        escuchó otra cosa.
        """
        return tuple(self.data["words"].get(language.split("-")[0].lower(), [])[-WORDS_FOR_CLAUDE:])

    def learn_words(self, found: list[str], language: str) -> None:
        """Palabras propias del jugador (nombres, jerga, las que Whisper no reconocía). No hacen falta las comunes
        ("que", "ese").
        """
        found = [word.strip() for word in found if len(word.strip()) > 2 and not _common(word.strip())]
        if not found:
            return
        language = language.split("-")[0].lower()
        with self._lock:
            known = [w for w in self.data["words"].get(language, []) if w.casefold() not in
                     {f.casefold() for f in found}]
            self.data["words"][language] = [*known, *found][-MAX_WORDS:]
        self.save()

    def learn_phrase(self, text: str, language: str) -> None:
        text = text.strip()
        if len(_WORDS.findall(text)) < 2 or not looks_clean(text):
            return
        language = language.split("-")[0].lower()
        with self._lock:
            phrases = [p for p in self.data["phrases"].get(language, []) if phrase_key(p) != phrase_key(text)]
            self.data["phrases"][language] = [*phrases, text][-MAX_PHRASES:]
        self.save()

    # ------------------------------------------------------------ Claude: cómo querés sonar
    def examples(self, target: str) -> tuple[tuple[str, str], ...]:
        pairs = self.data["examples"].get(target.split("-")[0].lower(), [])[-EXAMPLES_IN_PROMPT:]
        return tuple((said, wanted) for said, wanted in pairs)

    def approve(self, said: str, wanted: str, target: str, language: str) -> None:
        """Lo que dijo el jugador (corregido si hizo falta) y el resultado esperado: se aprende todo."""
        said, wanted = said.strip(), wanted.strip()
        if not said or not wanted:
            return
        target = target.split("-")[0].lower()
        with self._lock:
            pairs = [p for p in self.data["examples"].get(target, []) if phrase_key(p[0]) != phrase_key(said)]
            self.data["examples"][target] = [*pairs, [said, wanted]][-MAX_EXAMPLES:]
            self._keep(said, target, wanted)
        self.learn_phrase(said, language)

    # ------------------------------------------------------------ frases ya traducidas
    def saved(self, text: str, target: str) -> str | None:
        return self.data["saved"].get(f"{target.split('-')[0].lower()}|{phrase_key(text)}")

    def remember(self, text: str, target: str, translation: str) -> None:
        if not translation.strip() or len(_WORDS.findall(text)) > SAVE_UP_TO_WORDS or not looks_clean(text):
            return
        with self._lock:
            self._keep(text, target.split("-")[0].lower(), translation.strip())
        self.save()

    def _keep(self, text: str, target: str, translation: str) -> None:
        saved = self.data["saved"]
        key = f"{target}|{phrase_key(text)}"
        saved.pop(key, None)
        saved[key] = translation
        while len(saved) > MAX_SAVED:
            saved.pop(next(iter(saved)))

    # ------------------------------------------------------------ tu voz de siempre
    def learn_melody(self, melody: Melody | None) -> None:
        if melody is None or melody.voiced_s < 0.5:
            return
        with self._lock:
            known = self.data["melody"]
            count = known.get("count", 0)
            weight = 1 / (count + 1) if count < 20 else 0.05  # promedio, y luego se adapta gradualmente
            for key, value in (("pitch", melody.pitch), ("level", melody.level), ("effort", melody.effort)):
                known[key] = known.get(key, value) * (1 - weight) + value * weight
            known["count"] = count + 1
        self.save()

    def usual(self) -> tuple[float, float, float] | None:
        """(tono, volumen, esfuerzo) de la voz habitual; None si aún no se conoce."""
        known = self.data["melody"]
        if known.get("count", 0) < MIN_MELODIES or "effort" not in known:
            return None
        return known["pitch"], known["level"], known["effort"]

    def intonation(self, melody: Melody | None) -> str:
        """Describe cómo se dijo la frase, con los umbrales del jugador si ya se aprendieron (si no, los
        generales).
        """
        if not melody:
            return ""
        calibration = dict(self.data["calibration"])
        if "question_rise" in calibration:
            low, high = QUESTION_RISE_RANGE
            calibration["question_rise"] = min(high, max(low, calibration["question_rise"]))
        return melody.kind(self.usual(), **calibration)

    def calibrate(self, found: dict[str, float]) -> None:
        with self._lock:
            self.data["calibration"].update({key: value for key, value in found.items()
                                             if key in ("question_rise", "shout_db", "exclaim_db")})
            self._clean()
        self.save()

    def set_models(self, scores, chosen: str) -> None:
        """Puntaje de comprensión de cada modelo con las grabaciones del jugador y cuál quedó elegido."""
        with self._lock:
            self.data["models"] = {"puntajes": {s.name: {"accuracy": round(s.accuracy, 3), "seconds": round(s.seconds, 2)}
                                                for s in scores}, "elegido": chosen}
        self.save()

    # ------------------------------------------------------------ tiempos
    def note_times(self, times: dict[str, float]) -> None:
        with self._lock:
            known = self.data["times"]
            for stage, seconds in times.items():
                previous = known.get(stage)
                known[stage] = seconds if previous is None else previous * 0.8 + seconds * 0.2
        self.save()

    def summary(self) -> dict[str, int]:
        return {
            "frases": sum(len(p) for p in self.data["phrases"].values()),
            "ejemplos": sum(len(p) for p in self.data["examples"].values()),
            "guardadas": len(self.data["saved"]),
            "voz": int(self.data["melody"].get("count", 0)),
            "palabras": sum(len(w) for w in self.data["words"].values()),
        }

    def forget(self) -> None:
        with self._lock:
            self.data = self._empty()
        self.save()

    def save(self) -> None:
        with self._lock:
            payload = json.dumps(self.data, ensure_ascii=False)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(self.path)
        except OSError:
            log.debug("No se pudo guardar el perfil de voz", exc_info=True)
