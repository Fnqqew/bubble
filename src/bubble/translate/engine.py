"""Motor de traducción: decide si hace falta traducir, detecta jerga y variantes, usa cache y contexto."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import Counter, deque
from dataclasses import replace
from typing import Callable

from ..config import Config
from .batcher import Batcher
from .base import ChatLine, DeltaCallback, Direction, Mode, TranslationRequest, TranslationResult, clamp_tone
from .cache import TranslationCache
from .langdetect import (LanguageDetector, foreign_words, is_filtered, is_universal, known_anywhere,
                         native_by_words, without_gaming, words_in)
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
# Voz: si el carril rápido no terminó en este tiempo, se le pregunta también al del chat y gana el primero. Una
# traducción de voz tarda ~1,6 s (medido); a veces 3 o 4 s: ahí sirve.
HEDGE_AFTER_S = 2.3
log = logging.getLogger(__name__)


class Translator:
    def __init__(self, config: Config, router: Router, detector: LanguageDetector | None = None,
                 voice_router: Router | None = None) -> None:
        """`voice_router`: el carril de la voz (su propia sesión, sin pensar antes de responder): lo que decís y lo que
        te dicen por voz no hace fila detrás del chat."""
        self.config = config
        self.hedge = True  # si la voz tarda, probar también por el carril del chat (ver _hedged)
        self.router = router
        self.voice_router = voice_router
        self._voice_lane: asyncio.Task | None = None
        # El último idioma en el que hablaste o escribiste: se mantiene (antes, en "automático", se elegía de nuevo en
        # cada mensaje según el chat y cambiaba solo).
        self.last_target: str | None = None
        # Pedidos en curso por texto: si el chat y la burbuja piden el mismo mensaje a la vez, se hace uno solo.
        self._inflight: dict[tuple[str, str], asyncio.Future] = {}
        # Cómo querés sonar: devuelve pares (dijiste, quedó bien) aprobados en la página Pruebas para ese idioma.
        self.examples_for: Callable[[str], tuple[tuple[str, str], ...]] | None = None
        # Tus palabras y nombres (perfil de voz), para que Claude entienda lo que Whisper escuchó mal.
        self.vocabulary_for: Callable[[str], tuple[str, ...]] | None = None
        self.detector = detector or LanguageDetector()
        self.cache = TranslationCache(config.translation.cache_size, config.translation.cache_max_words)
        self.history: deque[ChatLine] = deque(maxlen=max(1, config.translation.context_lines))
        self.batcher = Batcher(router, workers=config.claude.pool_size)
        self._incoming_langs: deque[str] = deque(maxlen=30)
        self._incoming_regions: deque[tuple[str, str]] = deque(maxlen=30)
        self._speaker_langs: dict[str, deque[str]] = {}  # idioma de lo último que escribió cada jugador (seguro)

    async def start(self) -> None:
        await asyncio.gather(asyncio.to_thread(self.detector.load), self.router.start())
        self.batcher.start()
        if self.config.voice.speak or self.config.voice.subtitles or self.config.roblox.translate_bubbles:
            self.start_voice()  # el carril rápido: la voz y las burbujas

    def start_voice(self) -> None:
        """Abre el carril de la voz en segundo plano (y lo deja caliente). Mientras abre, la voz usa el del chat."""
        if self.voice_router is not None and self._voice_lane is None:
            self._voice_lane = asyncio.get_running_loop().create_task(self._open_voice_lane())

    async def _open_voice_lane(self) -> None:
        await self.voice_router.start()
        for provider in self.voice_router.providers:
            if hasattr(provider, "warm_up"):
                await provider.warm_up()

    def _voice_ready(self) -> bool:
        lane = self._voice_lane
        return lane is not None and lane.done() and not lane.cancelled() and lane.exception() is None

    async def close(self) -> None:
        await self.batcher.close()
        if self._voice_lane is not None:
            self._voice_lane.cancel()
            await self.voice_router.close()
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
        """Idioma al que se traduce lo que escribís o decís: el elegido; en "automático", el último que usaste (se
        mantiene); y si todavía no usaste ninguno, el más usado en el chat."""
        configured = self.config.user.outgoing_language
        if configured and configured != "auto":
            return split_locale(configured)[0]
        if self.last_target:
            return self.last_target
        languages = self.chat_languages()
        return languages[0] if languages else "en"

    def remember_target(self, language: str) -> None:
        """Hablaste o escribiste en este idioma: es el que se usa la próxima vez."""
        code = split_locale(language)[0] if language else ""
        if code.isalpha():  # no "*" (todos los del chat)
            self.last_target = code

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
        from_speech: bool = False,
        intonation: str = "",
        fast: bool = False,
    ) -> TranslationResult:
        """`on_pending` se llama (sin esperar) justo antes de pedirle la traducción a Claude:
        sirve para reservar el lugar del mensaje en pantalla, en el orden del chat. `from_speech`: lo dijeron por voz
        (va por el carril rápido y Claude sabe que puede haber palabras mal entendidas). `fast`: sin esperar a juntarse
        con otros mensajes y por el carril rápido si está abierto (las burbujas: se ven al lado del jugador)."""
        lang, region = self.my_locale
        return await self._translate(text, lang, region, "incoming", speaker, on_delta, on_pending=on_pending,
                                     from_speech=from_speech, intonation=intonation, fast=fast)

    async def translate_outgoing(
        self,
        text: str,
        target_lang: str | None = None,
        on_delta: DeltaCallback | None = None,
        tone: int | None = None,
        spoken: bool = False,
        from_speech: bool = False,
        intonation: str = "",
    ) -> TranslationResult:
        """`spoken`: se va a decir en voz (sin abreviaturas de chat, en la escritura del idioma). `from_speech`: lo
        dijiste vos por el micrófono (Whisper). Lo que va a voz usa el carril rápido."""
        target = target_lang or self.outgoing_target()
        lang, region = split_locale(target)
        if "-" not in target:
            region = self.outgoing_region(lang)
        tone = clamp_tone(tone if tone is not None else self.config.user.tone)
        examples = self.examples_for(lang) if self.examples_for else ()
        vocabulary = self.vocabulary_for(self.my_locale[0]) if (from_speech and self.vocabulary_for) else ()
        return await self._translate(text, lang, region, "outgoing", MY_SPEAKER, on_delta, tone, spoken=spoken,
                                     from_speech=from_speech, intonation=intonation, examples=examples,
                                     vocabulary=vocabulary)

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
        from_speech: bool = False,
        intonation: str = "",
        examples: tuple[tuple[str, str], ...] = (),
        vocabulary: tuple[str, ...] = (),
        fast: bool = False,
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

        detection = self.detector.detect(without_gaming(text))  # "pvp" o "lag" no hacen inglés a un mensaje
        source = detection.lang if detection else None
        confident = bool(detection and detection.is_confident(SKIP_CONFIDENCE))
        if len(hint_langs) == 1 and not (detection and detection.is_confident(TRACK_CONFIDENCE)):
            source = next(iter(hint_langs))  # la jerga identifica el idioma mejor que el detector
        if direction == "incoming":
            if source and (hints or (detection and detection.is_confident(TRACK_CONFIDENCE))):
                self._incoming_langs.append(source)
            self._incoming_regions.extend((h.lang, h.region) for h in hints if h.region)

        mode: Mode = "translate"
        tokens = text.split()
        # Una palabra suelta de otro idioma en un mensaje en tu idioma ("tal vez see") no lo vuelve extranjero.
        stray = foreign_words(text, target)
        foreign = any(h.lang != target for h in hints) or len(stray) >= max(1, (len(tokens) + 1) // 2)
        if direction == "incoming" and source and detection and detection.is_confident(TRACK_CONFIDENCE):
            self._speaker_langs.setdefault(speaker, deque(maxlen=4)).append(source)
        # Ya está en el idioma del lector: lo dice el detector con seguridad, o casi todas sus palabras son
        # comunes en ese idioma (clave para mensajes cortos como "hola", "dale voy", "todo bien?").
        looks_native = (source == target and (confident or hints)) or native_by_words(text, target, LEXICAL_SKIP)
        if direction == "incoming" and not looks_native and not foreign and not confident:
            looks_native = self._short_from_my_side(text, speaker, target)
        if not foreign and looks_native:
            source = target
            if self.config.translation.adapt_slang and self._foreign_region(hints, target, region):
                mode = "adapt"  # mismo idioma, pero con jerga de otro país
            else:
                self.history.append(ChatLine(speaker, text))
                return result(text, "same_language", source)

        # Cómo se dijo cuenta: "vamos a la torre" preguntado no se traduce igual que afirmado.
        cache_key = (f"{target}-{region}:{mode}:{direction}:{tone if direction == 'outgoing' else ''}:{spoken}:"
                     f"{intonation}")
        if (cached := self.cache.get(text, cache_key)) is not None:
            self.history.append(ChatLine(speaker, text))
            return result(cached, "cache", source)
        flight = (" ".join(text.casefold().split()), cache_key)
        if (shared := self._inflight.get(flight)) is not None:
            # Ya se está traduciendo (el mismo mensaje en el chat y en la burbuja): se espera ese mismo pedido.
            if on_pending:
                on_pending()
            shared_result = await asyncio.shield(shared)
            return replace(shared_result, total_s=time.perf_counter() - start)
        pending = asyncio.get_running_loop().create_future()
        self._inflight[flight] = pending
        try:
            finished = await self._ask(text, target, region, direction, speaker, source, mode, hints, tone, spoken,
                                       from_speech, intonation, examples, vocabulary, cache_key, on_delta,
                                       on_pending, fast, result)
        except BaseException as exc:
            finished = result(text, "error", source, error=str(exc) or type(exc).__name__)
            pending.set_result(finished)
            raise
        finally:
            self._inflight.pop(flight, None)
        if not pending.done():
            pending.set_result(finished)
        return finished

    async def _ask(self, text, target, region, direction, speaker, source, mode, hints, tone, spoken, from_speech,
                   intonation, examples, vocabulary, cache_key, on_delta, on_pending, fast, result) -> TranslationResult:
        """El pedido a Claude (ya se sabe que hace falta: no estaba en caché ni en curso)."""
        request = TranslationRequest(
            text, target, direction, speaker, tuple(self.history),
            target_region=region, mode=mode, slang_hints=_hint_tuples(hints), tone=tone, spoken=spoken,
            from_speech=from_speech, intonation=intonation, examples=tuple(examples), vocabulary=tuple(vocabulary),
        )
        # Se agrega al contexto ya (no al terminar) para que el siguiente mensaje del chat lo tenga en cuenta.
        self.history.append(ChatLine(speaker, text))
        if on_pending:
            on_pending()
        try:
            routed = await self._route(request, on_delta, fast)
            if looks_wrong(text, routed.text, request.context):
                # Traducción sospechosa (mezcló otros mensajes o inventó texto): reintento sin contexto.
                retry = replace(request, context=())
                routed = await self.router.translate(retry)
                if looks_wrong(text, routed.text, ()):
                    return result(text, "error", source, error="traducción dudosa, se muestra el original")
        except Exception as exc:  # noqa: BLE001 - la UI muestra el original con el error
            return result(text, "error", source, error=str(exc))

        if direction == "incoming" and mode == "translate" and unchanged(text, routed.text):
            # Claude lo devolvió igual: ya estaba en tu idioma (el detector dudó). No se muestra como traducción.
            self.cache.put(text, cache_key, text)
            return result(text, "same_language", target, routed.provider)
        self.cache.put(text, cache_key, routed.text)
        status = "adapted" if mode == "adapt" else "translated"
        return result(routed.text, status, source, routed.provider, ttft_s=routed.ttft_s)

    async def _route(self, request: TranslationRequest, on_delta: DeltaCallback | None, fast: bool = False):
        """Por dónde va cada pedido:
        - la voz (lo que decís, lo que te dicen, lo que escribís para decir) y las burbujas: carril rápido;
        - lo que escribís para el chat: directo, sin esperar a que se junten mensajes;
        - los mensajes del chat: en lotes (llegan en ráfagas)."""
        voice = request.spoken or request.from_speech or fast
        if voice and self._voice_ready():
            if not self.hedge:  # (sin Claude se paga por conexión abierta: nunca dos a la vez para lo mismo)
                return await self.voice_router.translate(request, on_delta)
            return await self._hedged(request, on_delta)
        if voice or request.direction == "outgoing":
            return await self.router.translate(request, on_delta)
        return await self.batcher.translate(request, on_delta)

    async def _hedged(self, request: TranslationRequest, on_delta: DeltaCallback | None = None):
        """Carril rápido; si tarda, también el del chat, y gana el primero que termine bien. El que pierde termina
        solo (cortarlo reiniciaría su sesión). Con `on_delta` (la voz se dice de a oraciones), los pedazos que se
        pasan son solo los del carril que empezó a responder primero, y gana ese: así nunca se mezclan dos
        traducciones."""
        owner: list[str] = []

        def relay(lane: str):
            if on_delta is None:
                return None

            def forward(chunk: str) -> None:
                if not owner:
                    owner.append(lane)
                if owner[0] == lane:
                    on_delta(chunk)

            return forward

        first = asyncio.ensure_future(self.voice_router.translate(request, relay("voz")))
        done, _pending = await asyncio.wait({first}, timeout=HEDGE_AFTER_S)
        if first in done:
            if first.exception() is None:
                return first.result()
            if owner:  # ya se dijo algo de esta traducción: no se empieza otra encima
                raise first.exception()
            return await self.router.translate(request, relay("chat"))
        if owner:  # el carril rápido ya está respondiendo: se lo espera (sin otro pedido)
            return await first
        second = asyncio.ensure_future(self.router.translate(request, relay("chat")))
        tasks = {"voz": first, "chat": second}
        pending = {first, second}
        error: BaseException | None = None
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if owner and task is not tasks[owner[0]]:
                    continue  # terminó el otro, pero la voz viene siguiendo a este
                if task.exception() is None:
                    for other in pending:
                        other.add_done_callback(_forget)
                    if task is second:
                        log.info("Voz: respondió antes el carril del chat")
                    return task.result()
                error = task.exception()
                if owner:
                    raise error
            if owner and tasks[owner[0]] not in pending and tasks[owner[0]].done():
                chosen = tasks[owner[0]]
                if chosen.exception() is None:
                    return chosen.result()
                raise chosen.exception()
        raise error or RuntimeError("No se pudo traducir")

    def _short_from_my_side(self, text: str, speaker: str, target: str) -> bool:
        """Un mensaje corto que el detector no sabe de qué idioma es ("alm", "visito", "sofiiii"). Se deja como está si
        ese jugador viene escribiendo en tu idioma, o si es una sola palabra que no es de ningún idioma conocido (un
        nombre, una risa, un error de tipeo). Antes iban a Claude, que los "traducía" a tu español."""
        tokens = words_in(text)
        if not tokens or len(tokens) > 4:
            return False
        recent = list(self._speaker_langs.get(speaker, ()))
        if recent and recent.count(target) * 2 > len(recent):
            return True
        return len(tokens) == 1 and not known_anywhere(tokens[0])

    @staticmethod
    def _foreign_region(hints: list[Slang], lang: str, region: str) -> bool:
        return any(h.lang == lang and h.region and h.region != region for h in hints)


def _forget(task: asyncio.Future) -> None:
    if not task.cancelled():
        task.exception()  # que no avise "nunca se leyó el error"


_WORD = re.compile(r"\w+", re.UNICODE)


def unchanged(original: str, translation: str) -> bool:
    """La "traducción" dice lo mismo que el original (mismas palabras, sin contar mayúsculas ni signos)."""
    before = _WORD.findall(original.casefold())
    after = _WORD.findall(translation.casefold())
    return bool(before) and before == after


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
