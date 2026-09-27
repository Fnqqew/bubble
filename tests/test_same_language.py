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
    "wer will traden?", "no mames wey",
])
async def test_messages_in_other_languages_are_translated(detector, text):
    translator, provider = make(detector)
    result = await translator.translate_incoming(text, "Pedro")
    assert result.status in {"translated", "adapted"}, text
    assert len(provider.requests) == 1
