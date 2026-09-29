"""Lo que Bubble aprende de cómo hablás, para entenderte mejor y traducir más rápido cuanto más lo usás.

- Tus palabras (tus nombres, tu jerga, las que corregiste) y tus frases (solo las que
  confirmaste vos): se le pasan a Claude, que con eso entiende qué quisiste decir aunque Whisper haya escuchado otra
  cosa. A Whisper NO: con una lista de palabras o frases de ejemplo largas, en frases cortas ("hola") inventaba o
  repetía ("Hola Hola Hola", "Podla"); medido. Whisper recibe solo un ejemplo fijo y corto (voice/speech.py).
- Cuánto sube tu voz al preguntar y cómo suena tu grito (si lo aprendió): cada uno pregunta y grita distinto.
- Cómo querés sonar: las traducciones que aprobaste o corregiste en Pruebas; Claude las usa de modelo.
- Frases ya traducidas: si volvés a decir lo mismo ("dale, esperame"), sale al instante, sin preguntarle a Claude.
- Tu voz de siempre (tono y volumen): para darse cuenta cuando exclamás o gritás.
- Cuánto tarda cada paso, para mostrarlo en Pruebas.

Solo se aprende lo seguro: nada con palabras repetidas ni cosas que no son palabras ("yonna kiona giona"). Lo que se
había aprendido así antes se limpia solo al abrir (ver `_clean`).

Se guarda en %LOCALAPPDATA%\\Bubble\\perfil_voz.json (solo en tu PC). Se borra desde la página Pruebas.
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
SAVE_UP_TO_WORDS = 8  # frases más largas dependen del contexto: no se reusan
MIN_MELODIES = 5  # con menos frases no se sabe cómo hablás normalmente
MAX_WORDS = 120
WORDS_FOR_CLAUDE = 60
QUESTION_RISE_RANGE = (0.5, 4.0)  # una pregunta tiene que SUBIR: antes podía quedar negativo y todo era pregunta
_WORDS = re.compile(r"\w+", re.UNICODE)


def default_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    return Path(base) / "Bubble" / "perfil_voz.json"


def phrase_key(text: str) -> str:
    """Mismas palabras = misma frase (sin mayúsculas ni signos), pero una pregunta no es lo mismo que una afirmación, ni
    un grito lo mismo que algo dicho tranquilo."""
    words = " ".join(_WORDS.findall(text.casefold()))
    return words + ("?" if "?" in text else "") + ("!" if "!" in text else "")


def looks_clean(text: str) -> bool:
    """¿Se puede aprender? No si repite palabras seguidas ("Hola Hola Hola", "comandas, comandas") ni si casi nada son
    palabras conocidas ("yonna kiona giona giona"): eso es Whisper inventando, no vos hablando."""
    from ..translate.langdetect import is_gaming, known_anywhere

    words = [w.casefold() for w in _WORDS.findall(text)]
    if not words:
        return False
    if any(words[i] == words[i + 1] for i in range(len(words) - 1)):
        return False
    half = len(words) // 2
    if half >= 2 and words[:half] == words[half:2 * half]:
        return False  # "como andas como andas"
    # La lista de palabras comunes es chica ("torre" no está): solo se rechaza si casi nada es conocido.
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
        """Saca lo que se aprendió mal (versiones anteriores aprendían sin revisar). True si cambió algo."""
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
        """Ejemplo para Whisper en ese idioma: uno fijo y corto, de cómo se habla (voseo, jerga, ¿? ¡!). Nada de lo
        aprendido: con eso Whisper inventaba en las frases cortas (medido)."""
        return EXAMPLES.get(language.split("-")[0].lower(), "")

    def vocabulary(self, language: str) -> tuple[str, ...]:
        """Tus palabras y nombres, para Claude: así entiende qué quisiste decir si Whisper escuchó otra cosa."""
        return tuple(self.data["words"].get(language.split("-")[0].lower(), [])[-WORDS_FOR_CLAUDE:])

    def learn_words(self, found: list[str], language: str) -> None:
        """Palabras tuyas (nombres, jerga, las que Whisper no te entendía). Las comunes ("que", "ese") no hacen falta."""
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
        """Lo que dijiste (corregido si hacía falta) y cómo tenía que quedar: se aprende todo."""
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
            weight = 1 / (count + 1) if count < 20 else 0.05  # promedio, y después se va adaptando de a poco
            for key, value in (("pitch", melody.pitch), ("level", melody.level), ("effort", melody.effort)):
                known[key] = known.get(key, value) * (1 - weight) + value * weight
            known["count"] = count + 1
        self.save()

    def usual(self) -> tuple[float, float, float] | None:
        """(tono, volumen, esfuerzo) de tu voz de siempre; None si todavía no se sabe."""
        known = self.data["melody"]
        if known.get("count", 0) < MIN_MELODIES or "effort" not in known:
            return None
        return known["pitch"], known["level"], known["effort"]

    def intonation(self, melody: Melody | None) -> str:
        """Cómo lo dijiste, con tus umbrales si ya los aprendió (si no, los de todos)."""
        return melody.kind(self.usual(), **self.data["calibration"]) if melody else ""

    def calibrate(self, found: dict[str, float]) -> None:
        with self._lock:
            self.data["calibration"].update({key: value for key, value in found.items()
                                             if key in ("question_rise", "shout_db", "exclaim_db")})
            self._clean()
        self.save()

    def set_models(self, scores, chosen: str) -> None:
        """Cuánto te entendió cada modelo con tus grabaciones y cuál quedó."""
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
