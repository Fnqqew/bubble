import pytest

from bubble.config import Config
from bubble.translate.engine import Translator
from bubble.translate.langdetect import LanguageDetector, foreign_words, lexical_share
from bubble.translate.router import Router
from fakes import FakeProvider


@pytest.fixture(scope="module")
def detector():
    d = LanguageDetector()
    d.load()
    return d


def make(detector):
    provider = FakeProvider(reply="traducido")
    config = Config()
    config.user.language = "es-AR"
    return Translator(config, Router([provider]), detector), provider


def test_lexicon():
    assert lexical_share("hola che, todo bien?", "es") == 1.0
    assert lexical_share("anyone wanna trade my dragon", "es") < 0.3
    assert foreign_words("vc tá aí?", "es") and "voce" not in foreign_words("hola", "es")
    assert foreign_words("the game is mid", "es")
    assert not foreign_words("dale, vamos a la base", "es")


@pytest.mark.parametrize("text", [
    "hola", "hola che, todo bien?", "dale, voy", "jaja no puede ser", "quién juega?", "bro vamos a la base",
    "gracias amigo", "alguien sabe donde esta el boss", "que onda", "si", "ya voy",
])
async def test_messages_in_my_language_are_skipped_without_claude(detector, text):
    translator, provider = make(detector)
    result = await translator.translate_incoming(text, "Juan")
    assert result.status in {"same_language", "universal", "local"}, text
    assert provider.requests == []


@pytest.mark.parametrize("text", [
    "anyone wanna trade my dragon", "vc tá aí?", "obrigado mano", "the boss is too op", "bhai kidher hai tu",
    "wer will traden?",
])
async def test_messages_in_other_languages_are_translated(detector, text):
    translator, provider = make(detector)
    result = await translator.translate_incoming(text, "Pedro")
    assert result.status in {"translated", "adapted"}, text
    assert len(provider.requests) == 1


# Mensajes reales de una grabación de Roblox (chat en español mezclado con un jugador que escribía en inglés).
@pytest.mark.parametrize("text", [
    "Sii", "ah", "nah", "aja", "Ahhhh", "nop", "Q", "Q PEDO", "tal vez see", "todo felicesxd", "NO HEMBRA",
    "SOY UN GATO", "MACHO PORFA", "entonces", "está muy devaluado el peso allá", "sofiiiiiii",
])
async def test_spanish_chat_from_a_real_game_is_left_alone(detector, text):
    translator, provider = make(detector)
    result = await translator.translate_incoming(text, "Ibarra")
    assert result.status in {"same_language", "universal", "local"}, (text, result.status, result.source_lang)
    assert provider.requests == []


async def test_short_unclear_messages_follow_what_that_player_writes(detector):
    translator, provider = make(detector)
    await translator.translate_incoming("está muy devaluado el peso allá", "Ibarra")
    for text in ("alm", "visito"):
        result = await translator.translate_incoming(text, "Ibarra")
        assert result.status == "same_language", text
    assert provider.requests == []


async def test_english_story_is_still_translated(detector):
    translator, provider = make(detector)
    for text in ('The mirror would answer, "You are the most beautiful of all women."',
                 "Once upon a time, there was a beautiful young princess named Snow White."):
        result = await translator.translate_incoming(text, "Andres")
        assert result.status == "translated", text
