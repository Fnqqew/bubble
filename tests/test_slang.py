from bubble.config import Config
from bubble.translate.base import TranslationRequest
from bubble.translate.engine import Translator
from bubble.translate.languages import describe, split_locale
from bubble.translate.prompt import build_system_prompt, build_user_prompt
from bubble.translate.router import Router
from bubble.translate.slang import laugh_only, scan
from fakes import FakeDetector, FakeProvider


def variants(text: str) -> set[str]:
    return {f"{h.lang}-{h.region}" for h in scan(text)}


def test_scan_identifies_language_and_country():
    assert variants("vlw mano, tmj") == {"pt-BR"}
    assert variants("no mames wey, neta?") == {"es-MX"}
    assert variants("che boludo, posta") == {"es-AR"}
    assert variants("Güey eso está chido") == {"es-MX"}  # sin distinguir mayúsculas ni acentos
    assert variants("mdr jsp") == {"fr-FR"}
    assert variants("wanna trade my pet") == set()  # las palabras comunes no cuentan como indicios


def test_laughs():
    assert laugh_only("kkkkkk")
    assert laugh_only("jajaja JAJA")
    assert laugh_only("ㅋㅋㅋ")
    assert laugh_only("wkwkwk")
    assert not laugh_only("kkkk que isso")


def test_locales():
    assert split_locale("es-AR") == ("es", "AR")
    assert split_locale("pt") == ("pt", "BR")
    assert "Rioplatense" in describe("es", "AR")
    assert "neutral" in describe("es", "")


def make(language="es-AR", detections=None, reply="ok"):
    provider = FakeProvider(reply=reply)
    config = Config()
    config.user.language = language
    return Translator(config, Router([provider]), FakeDetector(detections)), provider


async def test_laugh_is_translated_locally():
    translator, provider = make()
    result = await translator.translate_incoming("kkkkkk", "Pedro")
    assert (result.status, result.translation) == ("local", "jajaja")
    assert provider.requests == []


async def test_foreign_slang_beats_wrong_language_detection():
    # El detector clasifica el texto como español, pero "vlw" y "tmj" son portugués de Brasil.
    translator, provider = make(detections={"vlw mano tmj": ("es", 0.9)})
    result = await translator.translate_incoming("vlw mano tmj", "Pedro")
    assert result.status == "translated"
    assert result.source_lang == "es"  # aun con confianza alta del detector, se traduce
    assert provider.requests[0].slang_hints


async def test_my_language_with_other_country_slang_is_left_alone():
    translator, provider = make(detections={"no mames wey, esta chido": ("es", 0.9)})
    result = await translator.translate_incoming("no mames wey, esta chido", "Memo")
    assert result.status == "same_language"
    assert provider.requests == []


async def test_other_country_slang_is_adapted_when_asked():
    translator, provider = make(detections={"no mames wey, esta chido": ("es", 0.9)})
    translator.config.translation.adapt_slang = True
    result = await translator.translate_incoming("no mames wey, esta chido", "Memo")
    assert result.status == "adapted"
    assert provider.requests[0].mode == "adapt"
    assert provider.requests[0].target_region == "AR"


async def test_my_own_slang_is_not_adapted_for_me():
    translator, provider = make(detections={"che, posta que esta re bueno": ("es", 0.9)})
    result = await translator.translate_incoming("che, posta que esta re bueno", "Juan")
    assert result.status == "same_language"
    assert provider.requests == []


async def test_outgoing_uses_chat_variant():
    translator, provider = make(detections={"vlw mano": ("pt", 0.9), "dale, voy": ("es", 0.9)})
    await translator.translate_incoming("vlw mano", "Pedro")
    await translator.translate_outgoing("dale, voy")
    request = provider.requests[-1]
    assert (request.target_lang, request.target_region, request.direction) == ("pt", "BR", "outgoing")


async def test_chat_languages_follow_recent_messages_in_mixed_servers():
    detections = {f"msg{i}": (lang, 0.9) for i, lang in enumerate(["pt"] * 8 + ["en", "hi", "hi", "hi"])}
    translator, _ = make(detections=detections)
    for i in range(12):
        await translator.translate_incoming(f"msg{i}", "X")
    assert translator.chat_languages() == ["pt", "hi", "en"]
    for i in range(12, 20):  # la conversación cambia al hindi
        translator.detector.table[f"msg{i}"] = ("hi", 0.9)
        await translator.translate_incoming(f"msg{i}", "X")
    assert translator.outgoing_target() == "hi"


async def test_outgoing_tone_is_sent_and_cached_separately():
    translator, provider = make(detections={"dale, voy": ("es", 0.9)})
    await translator.translate_outgoing("dale, voy", "en", tone=1)
    await translator.translate_outgoing("dale, voy", "en", tone=5)
    again = await translator.translate_outgoing("dale, voy", "en", tone=5)
    assert [r.tone for r in provider.requests] == [1, 5]  # cada tono genera una traducción distinta
    assert again.status == "cache"
    translator.config.user.tone = 9  # fuera de rango: se ajusta a 5
    await translator.translate_outgoing("otra cosa", "en")
    assert provider.requests[-1].tone == 5


def test_tone_only_in_outgoing_prompt():
    outgoing = build_user_prompt(TranslationRequest("hola", "en", "outgoing", tone=2))
    incoming = build_user_prompt(TranslationRequest("hello", "es", "incoming", tone=2))
    assert "Tone level: 2 (Friendly)" in outgoing
    assert "Tone level" not in incoming
    assert "1 = Neutral" in build_system_prompt()


def test_each_tone_level_is_clearly_different():
    """Los tonos 1, 2 y 3 producían resultados casi idénticos porque el destino se describía "como escriben los gamers"
    incluso en el tono neutro.
    """
    neutral = build_user_prompt(TranslationRequest("che boludo", "es", "outgoing", target_region="AR", tone=1))
    native = build_user_prompt(TranslationRequest("che boludo", "es", "outgoing", target_region="AR", tone=5))
    assert "Rioplatense" in neutral and "che, re, posta" not in neutral and "drop every slang" in neutral
    assert "che, re, posta" in native and "heaviest local slang" in native
    assert "1: Hi, would you like to farm together?" in build_system_prompt()  # el mismo mensaje en los 5 niveles
    spoken = build_user_prompt(TranslationRequest("dale", "en", "outgoing", tone=1, spoken=True))
    assert "Keep the meaning and the requested tone level." in spoken


def test_prompts_include_variant_direction_and_hints():
    request = TranslationRequest(
        "posta?", "en", "outgoing", target_region="US", slang_hints=(("posta", "es-AR", "for real"),)
    )
    prompt = build_user_prompt(request)
    assert "Direction: outgoing." in prompt
    assert "American English" in prompt
    assert "posta (es-AR: for real)" in prompt
    system = build_system_prompt(explain_slang=True)
    assert "vlw = valeu: thanks" in system
    assert "parentheses" in system
    assert "parentheses" not in build_system_prompt(explain_slang=False)
