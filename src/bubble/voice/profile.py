"""Lo que Bubble aprende de cómo hablás, para entenderte mejor y traducir más rápido cuanto más lo usás.

- Tus frases (las que se entendieron con seguridad y las que confirmaste en la página Pruebas) y tus palabras (las que
  Whisper no te entendía en el entrenamiento, tus nombres, tu jerga): se le pasan a Whisper como ejemplo, y así
  escribe tus palabras, tus nombres y tu forma de hablar.
- Cuánto sube tu voz al preguntar y cómo suena tu grito (del entrenamiento): cada uno pregunta y grita distinto.
- Cómo querés sonar: las traducciones que aprobaste o corregiste en Pruebas; Claude las usa de modelo.
- Frases ya traducidas: si volvés a decir lo mismo ("dale, esperame"), sale al instante, sin preguntarle a Claude.
- Tu voz de siempre (tono y volumen): para darse cuenta cuando exclamás o gritás.
- Cuánto tarda cada paso, para mostrarlo en Pruebas.

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
PHRASES_IN_HINT = 6
MAX_EXAMPLES = 8
EXAMPLES_IN_PROMPT = 6
MAX_SAVED = 400
SAVE_UP_TO_WORDS = 8  # frases más largas dependen del contexto: no se reusan
MIN_MELODIES = 5  # con menos frases no se sabe cómo hablás normalmente
MAX_WORDS = 120
WORDS_IN_HINT = 45  # medido: con 20 casi no ayudaba; con 45, 6 a 16 puntos menos de error; con 80, más lento
_WORDS = re.compile(r"\w+", re.UNICODE)


def default_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    return Path(base) / "Bubble" / "perfil_voz.json"


def phrase_key(text: str) -> str:
    """Mismas palabras = misma frase (sin mayúsculas ni signos), pero una pregunta no es lo mismo que una afirmación, ni
    un grito lo mismo que algo dicho tranquilo."""
    words = " ".join(_WORDS.findall(text.casefold()))
    return words + ("?" if "?" in text else "") + ("!" if "!" in text else "")


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

    @staticmethod
    def _empty() -> dict:
        return {"phrases": {}, "examples": {}, "saved": {}, "melody": {}, "times": {}, "words": {},
                "calibration": {}, "training": {}}

    # ------------------------------------------------------------ Whisper: tus palabras
    def hint(self, language: str) -> str:
        """Ejemplo para Whisper en ese idioma: cómo se habla (con ¿? ¡!), tus palabras y tus últimas frases. Lo más
        tuyo va al final: si no entra todo, se recorta el principio."""
        language = language.split("-")[0].lower()
        words = self.data["words"].get(language, [])[-WORDS_IN_HINT:]
        mine = self.data["phrases"].get(language, [])[-PHRASES_IN_HINT:]
        vocabulary = (", ".join(words) + ".") if words else ""
        return " ".join(part for part in [EXAMPLES.get(language, ""), vocabulary, *mine] if part).strip()

    def learn_words(self, found: list[str], language: str) -> None:
        """Palabras tuyas (las que Whisper no te entendía, nombres, jerga): pasan a ser pistas para Whisper."""
        found = [word.strip() for word in found if word.strip()]
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
        if len(_WORDS.findall(text)) < 2:
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
        if not translation.strip() or len(_WORDS.findall(text)) > SAVE_UP_TO_WORDS:
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
        """Cómo lo dijiste, con tus umbrales si ya entrenaste (si no, los de todos)."""
        return melody.kind(self.usual(), **self.data["calibration"]) if melody else ""

    def calibrate(self, found: dict[str, float]) -> None:
        with self._lock:
            self.data["calibration"].update({key: value for key, value in found.items()
                                             if key in ("question_rise", "shout_db", "exclaim_db")})
        self.save()

    def set_models(self, scores, chosen: str) -> None:
        """Cuánto te entendió cada modelo con tus grabaciones y cuál quedó."""
        with self._lock:
            self.data["models"] = {"puntajes": {s.name: {"accuracy": round(s.accuracy, 3), "seconds": round(s.seconds, 2)}
                                                for s in scores}, "elegido": chosen}
        self.save()

    # ------------------------------------------------------------ entrenamiento
    def training_step(self, language: str) -> int:
        """Por qué frase va el entrenamiento en ese idioma (para seguir otro día)."""
        return int(self.data["training"].get(language.split("-")[0].lower(), 0))

    def set_training_step(self, language: str, step: int) -> None:
        with self._lock:
            self.data["training"][language.split("-")[0].lower()] = step
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
            "entrenada": int(bool(self.data["calibration"])),
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
