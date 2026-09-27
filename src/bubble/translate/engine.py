"""Motor de traducción: decide si hace falta traducir, detecta jerga y variantes, usa cache y contexto."""

from __future__ import annotations

import asyncio
import time
from collections import Counter, deque
from dataclasses import replace
from typing import Callable

from ..config import Config
from .batcher import Batcher
from .base import ChatLine, DeltaCallback, Direction, Mode, TranslationRequest, TranslationResult, clamp_tone
from .cache import TranslationCache
from .langdetect import LanguageDetector, foreign_words, is_filtered, is_universal, lexical_share
from .languages import DEFAULT_REGION, split_locale
from .router import Router
from .slang import Slang, laugh_for, laugh_only, scan

# Confianza mínima para considerar que un mensaje ya está en el idioma destino.
SKIP_CONFIDENCE = 0.6
# Confianza mínima para contar un idioma al estimar el idioma del servidor.
TRACK_CONFIDENCE = 0.5
# Si al menos esta fracción de las palabras son comunes en tu idioma (y ninguna delata otro), se saltea.
LEXICAL_SKIP = 0.6
MY_SPEAKER = "Yo"
MAX_HINTS = 6
# Mensajes recientes que se miran para decidir en qué idioma(s) escribir.
RECENT_MESSAGES = 12


class Translator:
    def __init__(self, config: Config, router: Router, detector: LanguageDetector | None = None) -> None:
        self.config = config
        self.router = router
        self.detector = detector or LanguageDetector()
        self.cache = TranslationCache(config.translation.cache_size, config.translation.cache_max_words)
        self.history: deque[ChatLine] = deque(maxlen=max(1, config.translation.context_lines))
        self.batcher = Batcher(router, workers=config.claude.pool_size)
        self._incoming_langs: deque[str] = deque(maxlen=30)
        self._incoming_regions: deque[tuple[str, str]] = deque(maxlen=30)

    async def start(self) -> None:
        await asyncio.gather(asyncio.to_thread(self.detector.load), self.router.start())
        self.batcher.start()

    async def close(self) -> None:
        await self.batcher.close()
        await self.router.close()

    @property
    def my_locale(self) -> tuple[str, str]:
        return split_locale(self.config.user.language)

    def chat_languages(self, recent: int = RECENT_MESSAGES) -> list[str]:
        """Idiomas (distintos del tuyo) de los últimos mensajes, del más usado al menos usado.

        En un mismo servidor puede haber varios idiomas: se mira solo lo reciente para seguir
        la conversación actual. A igual cantidad, gana el que habló más recientemente.
        """
        mine = self.my_locale[0]
        latest = [lang for lang in list(self._incoming_langs)[-recent:] if lang != mine]
        counts = Counter(latest)
        last_seen = {lang: i for i, lang in enumerate(latest)}
        return sorted(counts, key=lambda lang: (-counts[lang], -last_seen[lang]))

    def outgoing_target(self) -> str:
        """Idioma al que se traduce lo que escribís: el configurado o el más usado últimamente en el chat."""
        configured = self.config.user.outgoing_language
        if configured and configured != "auto":
            return split_locale(configured)[0]
        languages = self.chat_languages()
        return languages[0] if languages else "en"

    def outgoing_region(self, lang: str) -> str:
        """Variante del idioma destino: la configurada o la que más aparece en la jerga del chat."""
        configured = self.config.user.outgoing_language
        if configured and configured != "auto" and "-" in configured:
            conf_lang, conf_region = split_locale(configured)
            if conf_lang == lang:
                return conf_region
        counts = Counter(region for hint_lang, region in self._incoming_regions if hint_lang == lang)
        return counts.most_common(1)[0][0] if counts else DEFAULT_REGION.get(lang, "")

    async def translate_incoming(
        self,
        text: str,
        speaker: str = "",
        on_delta: DeltaCallback | None = None,
        on_pending: Callable[[], None] | None = None,
    ) -> TranslationResult:
        """`on_pending` se llama (sin esperar) justo antes de pedirle la traducción a Claude:
        sirve para reservar el lugar del mensaje en pantalla, en el orden del chat."""
        lang, region = self.my_locale
        return await self._translate(text, lang, region, "incoming", speaker, on_delta, on_pending=on_pending)

    async def translate_outgoing(
        self,
        text: str,
        target_lang: str | None = None,
        on_delta: DeltaCallback | None = None,
        tone: int | None = None,
        spoken: bool = False,
    ) -> TranslationResult:
        """`spoken`: se va a decir en voz (sin abreviaturas de chat, en la escritura del idioma)."""
        target = target_lang or self.outgoing_target()
        lang, region = split_locale(target)
        if "-" not in target:
            region = self.outgoing_region(lang)
        tone = clamp_tone(tone if tone is not None else self.config.user.tone)
        return await self._translate(text, lang, region, "outgoing", MY_SPEAKER, on_delta, tone, spoken=spoken)

    async def _translate(
        self,
        text: str,
        target: str,
        region: str,
        direction: Direction,
        speaker: str,
        on_delta: DeltaCallback | None,
        tone: int = 3,
        on_pending: Callable[[], None] | None = None,
        spoken: bool = False,
    ) -> TranslationResult:
        start = time.perf_counter()
        text = text.strip()

        def result(translation: str, status: str, source: str | None, provider: str = "", **extra) -> TranslationResult:
            return TranslationResult(
                original=text, translation=translation, source_lang=source, target_lang=target,
                provider=provider, status=status, total_s=time.perf_counter() - start, **extra,
            )

        if is_filtered(text):
            # Mensaje tapado por el filtro de Roblox (****): no hay nada que traducir ni mostrar.
            return result(text, "filtered", None)
        if not text or is_universal(text):
            return result(text, "universal", None)

        hints = scan(text)
        hint_langs = {h.lang for h in hints}
        if laugh_only(text):
            # Risas: se pasan al estilo del lector sin llamar a Claude (kkkk -> jajaja).
            source = next(iter(hint_langs), None)
            translated = text if source == target else laugh_for(target)
            return result(translated, "local", source)

        detection = self.detector.detect(text)
        source = detection.lang if detection else None
        confident = bool(detection and detection.is_confident(SKIP_CONFIDENCE))
        if len(hint_langs) == 1 and not (detection and detection.is_confident(TRACK_CONFIDENCE)):
            source = next(iter(hint_langs))  # la jerga identifica el idioma mejor que el detector
        if direction == "incoming":
            if source and (hints or (detection and detection.is_confident(TRACK_CONFIDENCE))):
                self._incoming_langs.append(source)
            self._incoming_regions.extend((h.lang, h.region) for h in hints if h.region)

        mode: Mode = "translate"
        foreign = any(h.lang != target for h in hints) or bool(foreign_words(text, target))
        # Ya está en el idioma del lector: lo dice el detector con seguridad, o casi todas sus palabras son
        # comunes en ese idioma (clave para mensajes cortos como "hola", "dale voy", "todo bien?").
        looks_native = (source == target and (confident or hints)) or lexical_share(text, target) >= LEXICAL_SKIP
        if not foreign and looks_native:
            source = target
            if self.config.translation.adapt_slang and self._foreign_region(hints, target, region):
                mode = "adapt"  # mismo idioma, pero con jerga de otro país
            else:
                self.history.append(ChatLine(speaker, text))
                return result(text, "same_language", source)

        cache_key = f"{target}-{region}:{mode}:{direction}:{tone if direction == 'outgoing' else ''}:{spoken}"
        if (cached := self.cache.get(text, cache_key)) is not None:
            self.history.append(ChatLine(speaker, text))
            return result(cached, "cache", source)

        request = TranslationRequest(
            text, target, direction, speaker, tuple(self.history),
            target_region=region, mode=mode, slang_hints=_hint_tuples(hints), tone=tone, spoken=spoken,
        )
        # Se agrega al contexto ya (no al terminar) para que el siguiente mensaje del chat lo tenga en cuenta.
        self.history.append(ChatLine(speaker, text))
        if on_pending:
            on_pending()
        try:
            routed = await self.batcher.translate(request, on_delta)
            if looks_wrong(text, routed.text, request.context):
                # Traducción sospechosa (mezcló otros mensajes o inventó texto): reintento sin contexto.
                retry = replace(request, context=())
                routed = await self.router.translate(retry)
                if looks_wrong(text, routed.text, ()):
                    return result(text, "error", source, error="traducción dudosa, se muestra el original")
        except Exception as exc:  # noqa: BLE001 - la UI muestra el original con el error
            return result(text, "error", source, error=str(exc))

        self.cache.put(text, cache_key, routed.text)
        status = "adapted" if mode == "adapt" else "translated"
        return result(routed.text, status, source, routed.provider, ttft_s=routed.ttft_s)

    @staticmethod
    def _foreign_region(hints: list[Slang], lang: str, region: str) -> bool:
        return any(h.lang == lang and h.region and h.region != region for h in hints)


def looks_wrong(original: str, translation: str, context: tuple[ChatLine, ...]) -> bool:
    """Detecta traducciones que claramente no corresponden al mensaje."""
    translated = translation.strip()
    if not translated:
        return True
    # Mucho más larga que el original: inventó o mezcló contenido (se tolera una aclaración breve).
    if len(translated) > 2.5 * len(original) + 45:
        return True
    # Incluye "Nombre:" de otro mensaje del contexto que no estaba en el original.
    lowered, original_lower = translated.casefold(), original.casefold()
    return any(
        line.speaker and f"{line.speaker.casefold()}:" in lowered and f"{line.speaker.casefold()}:" not in original_lower
        for line in context
    )


def _hint_tuples(hints: list[Slang]) -> tuple[tuple[str, str, str], ...]:
    unique: dict[str, tuple[str, str, str]] = {}
    for h in hints:
        variant = f"{h.lang}-{h.region}" if h.region else h.lang
        unique.setdefault(h.term.casefold(), (h.term, variant, h.meaning))
    return tuple(unique.values())[:MAX_HINTS]
