from bubble.config import Config
from bubble.translate.engine import Translator
from bubble.translate.router import Router
from fakes import FakeDetector, FakeProvider


def make(detections=None, reply="hola", fail=False):
    provider = FakeProvider(reply=reply, fail=fail)
    config = Config()
    config.user.language = "es"
    translator = Translator(config, Router([provider]), FakeDetector(detections))
    return translator, provider


async def test_translates_incoming_to_my_language_with_context():
    translator, provider = make({"hi there": ("en", 0.9), "how are you": ("en", 0.9)})
    await translator.translate_incoming("hi there", "Bob")
    result = await translator.translate_incoming("how are you", "Bob")
    assert result.status == "translated"
    assert result.target_lang == "es"
    assert provider.requests[-1].context[-1].text == "hi there"


async def test_skips_messages_already_in_my_language():
    translator, provider = make({"hola amigo": ("es", 0.9)})
    result = await translator.translate_incoming("hola amigo", "Juan")
    assert result.status == "same_language"
    assert result.translation == "hola amigo"
    assert provider.requests == []


async def test_low_confidence_detection_still_translates():
    translator, provider = make({"hello there": ("en", 0.2)})
    result = await translator.translate_incoming("hello there", "Bob")
    assert result.status == "translated"
    assert len(provider.requests) == 1


async def test_universal_and_cache_avoid_calls():
    translator, provider = make({"wanna trade?": ("en", 0.8)})
    assert (await translator.translate_incoming("gg ez", "Bob")).status == "universal"
    await translator.translate_incoming("wanna trade?", "Bob")
    again = await translator.translate_incoming("wanna trade?", "Ann")
    assert again.status == "cache"
    assert len(provider.requests) == 1


async def test_outgoing_target_follows_chat_language():
    translator, _ = make({"obrigado mano": ("pt", 0.9), "vamos lá": ("pt", 0.8), "hello": ("en", 0.9)})
    assert translator.outgoing_target() == "en"  # sin datos todavía
    for text in ("obrigado mano", "vamos lá", "hello"):
        await translator.translate_incoming(text, "X")
    assert translator.outgoing_target() == "pt"
    translator.config.user.outgoing_language = "fr"
    assert translator.outgoing_target() == "fr"


async def test_filtered_messages_are_not_translated():
    translator, provider = make({"hello **** my friend": ("en", 0.2)})
    for text in ("**********", "#### ####", "*** de *****", "**** ** you"):
        result = await translator.translate_incoming(text, "Bob")
        assert result.status == "filtered", text
    # Una palabra censurada aislada no impide traducir el resto del mensaje.
    assert (await translator.translate_incoming("hello **** my friend", "Bob")).status == "translated"
    assert len(provider.requests) == 1


async def test_error_returns_original_text():
    translator, _ = make({"hello": ("en", 0.9)}, fail=True)
    result = await translator.translate_incoming("hello", "Bob")
    assert result.status == "error"
    assert result.translation == "hello"
    assert result.error
