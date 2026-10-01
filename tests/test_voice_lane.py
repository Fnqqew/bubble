"""La voz del jugador, más rápida y mejor comprendida: carril propio con respaldo, idioma que se mantiene, sin traducir
lo que ya está en el idioma de destino, palabras propias del juego, y sin que la espera de turno cuente como "Claude
no responde".
"""

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
    assert result.translation == "hey (chat)"  # gana el que responde primero
    assert asyncio.get_running_loop().time() - started < 0.8


async def test_typed_messages_skip_the_batch_wait():
    translator, main, _voice = make()
    translator.batcher.start()
    await translator.translate_outgoing("hola che", "en")
    assert len(main.requests) == 1


async def test_the_language_you_used_is_kept():
    translator, _main, _voice = make(detections={"hi": ("en", 0.9), "oi": ("pt", 0.9)})
    translator.config.user.outgoing_language = "auto"
    translator._incoming_langs.extend(["pt", "pt", "pt"])  # el chat pasa a hablar portugués
    translator.remember_target("en-US")  # pero el jugador venía hablando en inglés
    assert translator.outgoing_target() == "en"
    translator.remember_target("*")  # "todos los del chat" no es un idioma
    assert translator.outgoing_target() == "en"
    translator.config.user.outgoing_language = "fr"  # idioma elegido a mano: tiene prioridad
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
            await asyncio.sleep(0.3)  # espera su turno (otra frase usa la sesión)
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
    assert [r.intonation for r in voice.requests] == ["question", ""]  # la segunda no se toma de la memoria


def test_claude_gets_your_words_to_undo_misheard_ones():
    text = build_user_prompt(TranslationRequest("vamos con lauti y bauti", "en", "outgoing", spoken=True,
                                                from_speech=True, vocabulary=("Lauti", "Bauti", "tradear")))
    assert "misheard" in text and "Lauti, Bauti, tradear" in text
    plain = build_user_prompt(TranslationRequest("hola", "en", "outgoing", vocabulary=("Lauti",)))
    assert "Lauti" not in plain  # solo cuando proviene de la voz


async def test_the_same_message_in_chat_and_bubble_is_translated_once():
    translator, main, voice = make(main_delay=0.2, detections={"hello there my friend": ("en", 0.9)})
    translator.batcher.start()
    chat = asyncio.create_task(translator.translate_incoming("hello there my friend", "Bob"))
    bubble = asyncio.create_task(translator.translate_incoming("hello there my friend", "", fast=True))
    from_chat, from_bubble = await asyncio.gather(chat, bubble)
    assert from_chat.translation == from_bubble.translation == "hey (chat)"
    assert len(main.requests) + len(voice.requests) == 1  # un único pedido para ambos


async def test_bubbles_skip_the_chat_batch_and_use_the_fast_lane():
    translator, main, voice = make(detections={"where is the boss": ("en", 0.9)})
    await _open_lane(translator)
    await translator.translate_incoming("where is the boss", "", fast=True)
    assert len(voice.requests) == 1 and main.requests == []


async def test_short_foreign_messages_with_game_words_are_translated():
    from bubble.translate.langdetect import native_by_words

    translator, main, _voice = make()
    for text in ("trade me", "carry me pls", "vamo pro boss", "wait", "same"):
        result = await translator.translate_incoming(text, "Player")
        assert result.status == "translated", text
    assert native_by_words("tengo lag", "es") and native_by_words("vamos a hacer pvp", "es")
    assert not native_by_words("carry me pls", "es")  # "me" y "pls" también se usan en español; "carry" no


async def test_what_others_say_races_both_fast_lanes_and_live_goes_by_its_own():
    main = FakeProvider("claude", reply="hola (chat)")
    voice = FakeProvider("claude-voz", reply="hola (voz)", delay=0.5)
    live = FakeProvider("claude-vivo", reply="hola (vivo)")
    config = Config()
    config.user.language = "es"
    config.voice.subtitles = True
    translator = Translator(config, Router([main]), FakeDetector({"hello there": ("en", 0.9)}),
                            voice_router=Router([voice]), live_router=Router([live]))
    translator.start_voice()
    await translator._voice_lane
    await translator._live_lane
    started = asyncio.get_running_loop().time()
    result = await translator.translate_incoming("hello there", "Voz 1", from_speech=True)
    assert result.translation == "hola (vivo)" and asyncio.get_running_loop().time() - started < 0.4
    assert len(voice.requests) == 1 and main.requests == []  # (los dos carriles rápidos, desde el comienzo)
    await translator.translate_incoming("hello there my friend", "Voz 1", from_speech=True, live=True)
    assert live.requests[-1].partial and len(voice.requests) == 1  # en vivo: solo su carril


async def test_quick_chat_shows_the_fast_one_first_and_keeps_the_precise_one():
    main = FakeProvider("claude", reply="hola, amigo")
    voice = FakeProvider("claude-voz", reply="hola amigo")
    config = Config()
    config.user.language = "es"
    translator = Translator(config, Router([main]), FakeDetector({"hello my friend": ("en", 0.9)}),
                            voice_router=Router([voice]))
    translator.batcher.start()
    translator.start_voice()
    await translator._voice_lane
    assert translator.fast_ready()
    draft = await translator.translate_incoming("hello my friend", "Ana", draft=True)
    assert draft.translation == "hola amigo" and draft.status == "translated"
    again = await translator.translate_incoming("hello my friend", "Ana", draft=True)
    assert again.status == "translated"  # la versión rápida no se guarda en la caché
    better = await translator.translate_incoming("hello my friend", "Ana", refine=True)
    assert better.translation == "hola, amigo" and len(main.requests) == 1
    assert [line.text for line in translator.history].count("hello my friend") == 2  # (una por cada borrador)
    cached = await translator.translate_incoming("hello my friend", "Ana", draft=True)
    assert cached.status == "cache" and cached.translation == "hola, amigo"  # la precisa sí queda guardada
