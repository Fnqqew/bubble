"""Tu voz, más rápida y mejor entendida: carril propio con respaldo, idioma que se mantiene, lo que ya está en tu idioma
no se traduce, palabras de juego, y que esperar turno no cuente como "Claude no responde"."""

import asyncio

from bubble.config import Config
from bubble.translate.base import TranslationRequest
from bubble.translate.engine import Translator, unchanged
from bubble.translate.prompt import build_user_prompt
from bubble.translate.router import SENT, Router
from fakes import FakeDetector, FakeProvider


def make(voice_delay=0.0, main_delay=0.0, reply="hey", detections=None):
    main = FakeProvider("claude", reply=f"{reply} (chat)", delay=main_delay)
    voice = FakeProvider("claude-voz", reply=reply, delay=voice_delay)
    config = Config()
    config.user.language = "es"
    translator = Translator(config, Router([main]), FakeDetector(detections), voice_router=Router([voice]))
    return translator, main, voice


async def _open_lane(translator):
    translator.start_voice()
    await translator._voice_lane


async def test_your_voice_goes_by_its_own_lane():
    translator, main, voice = make()
    await _open_lane(translator)
    result = await translator.translate_outgoing("dale, esperame", "en", spoken=True, from_speech=True)
    assert result.translation == "hey" and len(voice.requests) == 1 and main.requests == []
    request = voice.requests[0]
    assert request.from_speech and request.spoken


async def test_while_the_lane_opens_the_voice_uses_the_chat_lane():
    translator, main, voice = make()
    result = await translator.translate_outgoing("dale", "en", spoken=True, from_speech=True)
    assert result.translation == "hey (chat)" and voice.requests == []


async def test_a_slow_voice_lane_is_backed_up_by_the_chat_lane(monkeypatch):
    import bubble.translate.engine as engine

    monkeypatch.setattr(engine, "HEDGE_AFTER_S", 0.05)
    translator, main, voice = make(voice_delay=1.0)
    await _open_lane(translator)
    started = asyncio.get_running_loop().time()
    result = await translator.translate_outgoing("vamos", "en", spoken=True, from_speech=True)
    assert result.translation == "hey (chat)"  # ganó el que respondió primero
    assert asyncio.get_running_loop().time() - started < 0.8


async def test_typed_messages_skip_the_batch_wait():
    translator, main, _voice = make()
    translator.batcher.start()
    await translator.translate_outgoing("hola che", "en")
    assert len(main.requests) == 1


async def test_the_language_you_used_is_kept():
    translator, _main, _voice = make(detections={"hi": ("en", 0.9), "oi": ("pt", 0.9)})
    translator.config.user.outgoing_language = "auto"
    translator._incoming_langs.extend(["pt", "pt", "pt"])  # el chat ahora habla portugués…
    translator.remember_target("en-US")  # …pero vos venías hablando en inglés
    assert translator.outgoing_target() == "en"
    translator.remember_target("*")  # "todos los del chat" no es un idioma
    assert translator.outgoing_target() == "en"
    translator.config.user.outgoing_language = "fr"  # elegido a mano: manda eso
    assert translator.outgoing_target() == "fr"


async def test_an_unchanged_translation_means_it_was_already_your_language():
    translator, _main, _voice = make()
    translator.router.providers[0].reply = "che vamos al jefe"
    result = await translator.translate_incoming("che vamos al jefe", "Voz 1")
    assert result.status == "same_language"
    assert unchanged("¡Che, vamos al jefe!", "che vamos al jefe") and not unchanged("hi", "hola")


async def test_gaming_words_do_not_make_spanish_look_english():
    translator, main, _voice = make(detections={"vamos a hacer": ("es", 0.9)})
    for text in ("vamos a hacer pvp", "pvp?", "gg noob", "tengo lag"):
        result = await translator.translate_incoming(text, "Juan")
        assert result.status in ("same_language", "universal"), text
    assert main.requests == []


def test_speech_requests_tell_claude_how_it_was_said():
    text = build_user_prompt(TranslationRequest("vamos a la torre", "en", "outgoing", spoken=True, from_speech=True,
                                                intonation="question+shout",
                                                examples=(("dale", "okay, let's go"),)))
    assert "transcribed from speech" in text
    assert "voice rose at the end" in text and "SHOUTED" in text
    assert "<how_i_sound>" in text and "okay, let" in text


async def test_waiting_for_a_free_session_is_not_a_stall():
    class Busy(FakeProvider):
        reports_sent = True

        async def stream_batch(self, requests):
            self.requests.extend(requests)
            await asyncio.sleep(0.3)  # esperando turno (otra frase usa la sesión)
            yield SENT, ""
            yield 0, "hola"

    provider = Busy("claude")
    routed = await Router([provider], timeout_s=2.0, first_token_s=0.1).translate(
        TranslationRequest("hi", "es", "incoming"))
    assert routed.text == "hola" and len(provider.requests) == 1  # sin reintentos


async def test_the_same_words_said_differently_are_not_mixed_up():
    translator, _main, voice = make()
    await _open_lane(translator)
    await translator.translate_outgoing("vamos a la torre", "en", spoken=True, from_speech=True, intonation="question")
    await translator.translate_outgoing("vamos a la torre", "en", spoken=True, from_speech=True)
    assert [r.intonation for r in voice.requests] == ["question", ""]  # la segunda no sale de la memoria


def test_claude_gets_your_words_to_undo_misheard_ones():
    text = build_user_prompt(TranslationRequest("vamos con lauti y bauti", "en", "outgoing", spoken=True,
                                                from_speech=True, vocabulary=("Lauti", "Bauti", "tradear")))
    assert "misheard" in text and "Lauti, Bauti, tradear" in text
    plain = build_user_prompt(TranslationRequest("hola", "en", "outgoing", vocabulary=("Lauti",)))
    assert "Lauti" not in plain  # solo cuando viene de la voz


async def test_the_same_message_in_chat_and_bubble_is_translated_once():
    translator, main, voice = make(main_delay=0.2, detections={"hello there my friend": ("en", 0.9)})
    translator.batcher.start()
    chat = asyncio.create_task(translator.translate_incoming("hello there my friend", "Bob"))
    bubble = asyncio.create_task(translator.translate_incoming("hello there my friend", "", fast=True))
    from_chat, from_bubble = await asyncio.gather(chat, bubble)
    assert from_chat.translation == from_bubble.translation == "hey (chat)"
    assert len(main.requests) + len(voice.requests) == 1  # un solo pedido para los dos


async def test_bubbles_skip_the_chat_batch_and_use_the_fast_lane():
    translator, main, voice = make(detections={"where is the boss": ("en", 0.9)})
    await _open_lane(translator)
    await translator.translate_incoming("where is the boss", "", fast=True)
    assert len(voice.requests) == 1 and main.requests == []
