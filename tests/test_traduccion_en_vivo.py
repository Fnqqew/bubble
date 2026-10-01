"""Traducción en vivo de lo que dicen los demás: aparece mientras la persona habla, sin esperar la pausa."""

import asyncio
import sys

import pytest

from bubble.voice import captions
from bubble.voice.captions import CaptionBoard
from bubble.voice.live import Caption


class FakeTranslate:
    """Guarda los pedidos y responde cuando el test lo indica."""

    def __init__(self):
        self.calls = []

    def __call__(self, text, language, speaker, on_piece, on_done, intonation="", live=False):
        self.calls.append({"text": text, "live": live, "piece": on_piece, "done": on_done, "intonation": intonation})

    def answer(self, index, translation, pieces=()):
        call = self.calls[index]
        for piece in pieces:
            call["piece"](piece)
        call["done"](translation)


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    monkeypatch.setattr(captions, "LIVE_EVERY_S", 0.0)


def say(board, text, final=False, intonation="", number=1):
    board.caption(Caption(number, 0.0, 1.0, text, "en", 1, final, False, intonation))


def test_it_translates_while_they_speak_and_keeps_it_when_the_sentence_ends():
    fake = FakeTranslate()
    board = CaptionBoard("es-AR", fake)
    say(board, "hey")
    assert fake.calls == []  # (con una sola palabra todavía no)
    say(board, "hey guys")
    assert len(fake.calls) == 1 and fake.calls[0]["live"] and fake.calls[0]["text"] == "hey guys"
    say(board, "hey guys does anyone")  # un segundo pedido en vivo, en paralelo
    assert len(fake.calls) == 2 and fake.calls[1]["text"] == "hey guys does anyone"
    say(board, "hey guys does anyone know where")  # ya hay dos en curso: no se pide otro
    assert len(fake.calls) == 2
    fake.answer(1, "che, chicos, alguien")
    fake.answer(0, "che, chicos")  # llega tarde: no pisa la traducción más nueva
    line = board.visible()[0]
    assert line.translation == "che, chicos, alguien" and line.live and not line.done
    # al responder, como la persona siguió hablando, se traduce lo nuevo
    assert len(fake.calls) == 3 and fake.calls[2]["text"] == "hey guys does anyone know where"
    fake.answer(2, "che, chicos, ¿alguien sabe dónde")
    say(board, "hey guys does anyone know where", final=True)
    line = board.visible()[0]
    assert len(fake.calls) == 3  # la última traducción en vivo ya abarcaba la frase: no se pide otra
    assert line.done and not line.live and line.translation == "che, chicos, ¿alguien sabe dónde"


def test_without_its_own_lane_only_one_live_request_at_a_time():
    fake = FakeTranslate()
    board = CaptionBoard("es-AR", fake, parallel=1)
    say(board, "hey guys")
    say(board, "hey guys does anyone")
    assert len(fake.calls) == 1


def test_the_final_translation_replaces_the_live_one_without_emptying_the_subtitle():
    fake = FakeTranslate()
    board = CaptionBoard("es-AR", fake)
    say(board, "so basically what you")
    fake.answer(0, "entonces básicamente lo que")
    say(board, "so basically what you need to do is collect the gems", final=True, intonation="question")
    final = fake.calls[-1]
    assert not final["live"] and final["intonation"] == "question"
    final["piece"]("entonces")  # (los fragmentos no reemplazan lo que se ve: llega completa)
    assert board.visible()[0].translation == "entonces básicamente lo que"
    final["done"]("entonces básicamente tenés que juntar las gemas")
    line = board.visible()[0]
    assert line.translation == "entonces básicamente tenés que juntar las gemas" and line.done and not line.live


def test_a_late_live_translation_does_not_overwrite_the_final_one():
    fake = FakeTranslate()
    board = CaptionBoard("es-AR", fake)
    say(board, "wait for me at")
    say(board, "wait for me at the tower", final=True)
    fake.answer(1, "esperame en la torre")
    fake.answer(0, "esperame en")  # llega tarde: ya hay traducción final
    assert board.visible()[0].translation == "esperame en la torre"


def test_if_they_keep_talking_after_a_finished_sentence_the_live_translation_resumes():
    fake = FakeTranslate()
    board = CaptionBoard("es-AR", fake)
    board.caption(Caption(1, 0.0, 1.0, "follow me.", "en", 1, False, True))  # (parece terminada: se pide ya)
    assert len(fake.calls) == 1 and not fake.calls[0]["live"]
    say(board, "follow me. i know a shortcut to")
    assert len(fake.calls) == 2 and fake.calls[1]["live"]
    fake.answer(0, "seguime.")  # la traducción de lo anterior ya no corresponde
    fake.answer(1, "seguime. conozco un atajo a")
    assert board.visible()[0].translation == "seguime. conozco un atajo a"


def test_at_the_pause_the_live_translation_is_enough_if_it_covered_everything():
    fake = FakeTranslate()
    board = CaptionBoard("es-AR", fake)
    say(board, "wait for me at the tower")
    fake.answer(0, "esperame en la torre")
    board.caption(Caption(1, 0.0, 1.0, "wait for me at the tower", "en", 1, False, True))  # pausa: suena terminada
    say(board, "wait for me at the tower", final=True)
    assert len(fake.calls) == 1 and board.visible()[0].done


def test_if_the_last_live_translation_is_on_its_way_it_waits_for_it_instead_of_asking_again():
    fake = FakeTranslate()
    translated = []
    board = CaptionBoard("es-AR", fake, on_translated=translated.append)
    say(board, "wait for me at the tower")
    say(board, "wait for me at the tower", final=True)
    assert len(fake.calls) == 1  # (la traducción en vivo de ese mismo texto sigue en camino)
    fake.answer(0, "esperame en la torre")
    line = board.visible()[0]
    assert len(fake.calls) == 1 and line.done and not line.live and translated


def test_a_question_without_question_mark_still_gets_its_final_translation():
    fake = FakeTranslate()
    board = CaptionBoard("es-AR", fake)
    say(board, "are you coming to the tower")
    say(board, "are you coming to the tower", final=True, intonation="question")
    fake.answer(0, "venís a la torre")  # sin «?»: no respeta la entonación
    assert len(fake.calls) == 2 and not fake.calls[1]["live"] and fake.calls[1]["intonation"] == "question"
    fake.answer(1, "¿venís a la torre?")
    assert board.visible()[0].translation == "¿venís a la torre?"


def test_a_reply_closed_with_the_request_tag_does_not_show_the_tag():
    from bubble.translate.prompt import OutputFilter

    output = OutputFilter(1)
    parts = output.feed("<t1>¿Alguien me ayuda a pasar este salto, porfa?</m1>") + output.finish()
    assert "".join(text for _i, text in parts) == "¿Alguien me ayuda a pasar este salto, porfa?"


def test_without_live_translation_it_waits_for_the_pause_as_before():
    fake = FakeTranslate()
    board = CaptionBoard("es-AR", fake, live=False)
    say(board, "hey guys does anyone know where the door is")
    assert fake.calls == []
    say(board, "hey guys does anyone know where the door is", final=True)
    assert len(fake.calls) == 1 and not fake.calls[0]["live"]


def test_the_subtitle_marks_the_live_translation_as_unfinished():
    from bubble.ui.subtitles import _body

    fake = FakeTranslate()
    board = CaptionBoard("es-AR", fake)
    say(board, "so basically what you")
    fake.answer(0, "entonces básicamente lo que")
    assert _body(board.visible()[0])[0].endswith("…")


async def test_a_live_request_does_not_touch_the_chat_context_or_the_cache():
    sys.path.insert(0, "tests")
    from fakes import FakeProvider

    from bubble.config import Config
    from bubble.translate.engine import Translator
    from bubble.translate.router import Router

    provider = FakeProvider(reply="entonces básicamente lo que")
    config = Config()
    config.user.language = "es-AR"
    translator = Translator(config, Router([provider]))
    result = await translator.translate_incoming("so basically what you", "Voz 1", from_speech=True, live=True)
    assert result.status == "translated"
    assert not translator.history and translator.cache.get("so basically what you", "x") is None
    request = provider.requests[-1] if hasattr(provider, "requests") else None
    if request is not None:
        assert request.partial
    await asyncio.sleep(0)
