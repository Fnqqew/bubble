from bubble.capture.chat_parser import SpamFilter, is_system_message, parse_chat, parse_chat_items
from bubble.capture.ocr import OcrRow
from bubble.translate.base import ChatLine


def row(text: str, i: int, width: float) -> OcrRow:
    return OcrRow(text, top=5 + i * 24, height=18, left=10, width=width)


def test_long_message_across_three_lines_is_complete():
    # Captura real: el mensaje de Andres ocupa 3 líneas y la región calibrada es más ancha que el texto.
    rows = [
        row("[■] Andres: Once upon a time there was a little girl who wore", 0, 518),
        row("a beautiful red cloak. Her mother, who knew how to sew very", 1, 515),
        row("well,", 2, 40),
        row("[■] Andres: I made it for her. The girl wore it so often that", 3, 512),
        row("everyone called her Little Red Riding Hood.", 4, 380),
    ]
    # Región calibrada mucho más ancha (720) que el texto: el borde de corte aprendido (528) es el que vale.
    lines = [i.line for i in parse_chat_items(rows, frame_width=720, wrap_right=528)]
    assert lines == [
        ChatLine("Andres", "Once upon a time there was a little girl who wore a beautiful red cloak. "
                           "Her mother, who knew how to sew very well,"),
        ChatLine("Andres", "I made it for her. The girl wore it so often that everyone called her Little Red Riding Hood."),
    ]


def test_system_messages_are_detected_even_when_misread():
    rows = [
        row("[SYSTEM]: Insaan has added a comment to danger! (+25) ▸", 0, 520),
        row('"good morning"', 1, 120),
        row("[SYS TEM]: yappy has added a comment to Suratt_xyzzz! (+25)", 2, 525),
        row("TEM: Insaan has added a comment to toxiquemaybe!", 3, 400),
        row("[SYSTEM]: jody jo donated 10 to crisxlives!", 4, 380),
        row("n7r0pyy: Jydin", 5, 110),
    ]
    items = parse_chat_items(rows, frame_width=600)
    assert [i.kind for i in items] == ["system", "system", "system", "system", "player"]
    assert items[0].text.endswith('"good morning"')  # la continuación queda dentro del aviso, no suelta
    assert parse_chat(rows, frame_width=600) == [ChatLine("n7r0pyy", "Jydin")]


def test_flag_read_without_closing_bracket():
    rows = [row("[S Juan: buenas gente", 0, 200), row("[™ Luana: oi galera", 1, 200), row("[Team] Kai: go", 2, 120)]
    assert parse_chat(rows, frame_width=600) == [
        ChatLine("Juan", "buenas gente"), ChatLine("Luana", "oi galera"), ChatLine("Kai", "go"),
    ]


def test_chat_input_bar_is_never_a_message():
    rows = [row("Luana: me ajuda no obby pfv", 0, 518), row("To chat click here or press / key", 1, 300)]
    assert parse_chat(rows, frame_width=600, ) == [ChatLine("Luana", "me ajuda no obby pfv")]
    rows = [row("Juan: hola che", 0, 200), row("Para chatear, haz clic aquí o presiona la tecla /", 1, 380)]
    assert parse_chat(rows, frame_width=600) == [ChatLine("Juan", "hola che")]


def test_system_detection_does_not_flag_normal_chat():
    assert not is_system_message("Andres", "I made it for her")
    assert not is_system_message("Pedro_BR", "vlw mano, tmj kkkk")
    assert is_system_message("Servidor", "reinicio en 5 minutos")


def test_spam_filter():
    now = [0.0]
    spam = SpamFilter(clock=lambda: now[0])
    assert spam.check(ChatLine("A", "aaaaaaaaaaaaaaaa")) == "repetición de letras"
    assert spam.check(ChatLine("B", "plss donate")) is None
    now[0] = 1
    assert spam.check(ChatLine("B", "plsss donate")) is None
    now[0] = 2
    assert spam.check(ChatLine("B", "plss donatee")) == "mensaje repetido"
    for i, text in enumerate(["Kikuuu", "Kiiki", "Kiiku", "Donen", "Jydin"]):
        now[0] = 10 + i
        result = spam.check(ChatLine("n7r0pyy", text))
    assert result == "demasiados mensajes seguidos"
    assert spam.check(ChatLine("Andres", "I made it for her")) is None
