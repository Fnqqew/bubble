from PIL import Image, ImageDraw, ImageFont

from bubble.capture.bubbles import find_bubbles
from bubble.capture.chat_parser import ChatTracker, parse_chat_items
from bubble.capture.chat_watcher import ChatFrame, estimate_shift
from bubble.capture.ocr import OcrRow, OcrWord, prepare_chat_image
from bubble.geometry import Rect
from bubble.translate.base import ChatLine
from bubble.ui.inline import (
    ACCENT, CHAT_FILL, InlineChatView, Slot, chat_metrics, chat_slots, chat_spots, fit_bubble_text, layout_text,
    render_pill,
)


def words_row(text: str, top: float, left: float = 10, char_w: float = 8, height: float = 16) -> OcrRow:
    words, x = [], left
    for word in text.split():
        words.append(OcrWord(word, x, top, len(word) * char_w, height))
        x += (len(word) + 1) * char_w
    return OcrRow(text, top, height, left, x - left - char_w, tuple(words))


def test_patch_starts_after_the_name():
    row = words_row("jody jo: a lil off key", top=20)
    item = parse_chat_items([row], frame_width=400)[0]
    assert (item.speaker, item.text) == ("jody jo", "a lil off key")
    # "jody jo: " ocupa 9 caracteres de 8 px: el mensaje empieza cerca de x = 10 + 9*8 = 82.
    assert 74 <= item.text_left <= 90
    spot = chat_spots(item, frame_width=400)[0]
    assert spot.left == int(item.text_left) - 5  # tapa desde el espacio después de "Nombre:"
    assert spot.cover_right >= int(row.right) and spot.max_right == 406  # puede pasar un poco el borde calibrado
    assert chat_slots(item, frame_width=400)[0].right < 400  # el texto deja margen dentro de la píldora


def test_all_chat_pills_share_font_and_height():
    rows = [words_row("Jake: ngl this game is mid", 20, height=16), words_row("Luana: vlw mano", 44, height=21)]
    items = [parse_chat_items([row], frame_width=400)[0] for row in rows]
    size, row_height = chat_metrics(items)
    heights = {spot.height for item in items for spot in chat_spots(item, 400, row_height)}
    assert len(heights) == 1  # misma altura aunque el OCR midió 16 y 21


def test_translated_bubble_grows_instead_of_cutting_the_text():
    # Burbuja original de una línea ("brb gotta eat"); la traducción es bastante más larga.
    size, lines, width, height = fit_bubble_text("ya vuelvo, tengo que comer algo rápido", 116, 36, 1)
    assert not any("…" in line for line in lines)  # entra completa
    assert width >= 116 and height >= 36  # tapa toda la original
    assert size >= 13  # se lee (no la achicó hasta lo ilegible)
    short = fit_bubble_text("gg", 116, 36, 1)
    assert short[2:] == (116, 36)  # si entra, queda del tamaño de la original


def test_layout_fits_wraps_and_truncates():
    one = [Slot(0, 0, 400, 20)]
    size, lines = layout_text("hola che", one, 16)
    assert size == 16 and lines == ["hola che"]
    two = [Slot(0, 0, 120, 20), Slot(0, 20, 120, 40)]
    size, lines = layout_text("tenés que corregir un poco las notas", two, 16)
    assert len(lines) == 2 and all(lines)
    size, lines = layout_text("palabra " * 40, [Slot(0, 0, 100, 20)], 16)
    assert lines[0].endswith("…") and size <= 16


def test_pill_has_rounded_transparent_corners_accent_and_text():
    image = render_pill(200, 24, "hola che", 16, fill=CHAT_FILL, text_color=(246, 247, 250), accent=ACCENT)
    assert image.mode == "RGBA" and image.size == (200, 24)
    assert image.getpixel((0, 0))[3] < 60  # esquina redondeada: transparente
    assert image.getpixel((100, 2))[3] > 200  # fondo casi opaco: tapa el original
    r, g, b, _a = image.getpixel((4, 12))
    assert b > 200 and r < 140  # rayita azul de "traducido"
    assert max(image.convert("L").getextrema()) > 200  # hay letra blanca


def test_inline_view_tracks_entries_by_message():
    tracker = ChatTracker()
    view = InlineChatView(None, tracker.same_message)
    view.pending(1, ChatLine("jody jo", "a lil off key"))
    view.delta(1, "un poco ")
    assert view.find(ChatLine("jody j0", "a lil off key")).text == "un poco "  # el OCR leyó distinto el nombre
    view.final(1, ChatLine("jody jo", "a lil off key"), "un poco desafinado")
    assert view.find(ChatLine("jody jo", "a lil off key")).status == "done"
    view.final(2, ChatLine("Juan", "hola che"), None)  # ya estaba en tu idioma: no se tapa
    assert view.find(ChatLine("Juan", "hola che")).status == "hidden"
    assert view.find_text("a lil off key").key == 1  # las burbujas reutilizan la traducción del chat


class FakeLayer:
    def __init__(self) -> None:
        self.visible: dict = {}
        self.hidden_all = 0

    def show(self, key, image, x, y) -> None:
        self.visible[key] = (x, y)

    def keep_only(self, keys) -> None:
        self.visible = {k: v for k, v in self.visible.items() if k in keys}

    def hide_all(self) -> None:
        self.visible.clear()
        self.hidden_all += 1


def chat_frame(lines: list[tuple[str, str, float]], on_screen: list[tuple[str, str, float]] | None = None) -> ChatFrame:
    """Captura con las líneas `on_screen` dibujadas (por defecto, las mismas que leyó el OCR)."""
    items = [parse_chat_items([words_row(f"{who}: {text}", top)], frame_width=400)[0] for who, text, top in lines]
    image = Image.new("RGB", (400, 300), (30, 34, 44))
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("arialbd.ttf", 14)
    for who, text, top in lines if on_screen is None else on_screen:
        draw.text((10, top), f"{who}: {text}", font=font, fill=(255, 255, 255))
    return ChatFrame(image, Rect(100, 100, 400, 300), items)


def translated_view(*lines: tuple[str, str]) -> InlineChatView:
    view = InlineChatView(None, ChatTracker().same_message)
    view.layer = FakeLayer()
    for key, (who, text) in enumerate(lines):
        view.final(key, ChatLine(who, text), f"traducción {key}")
    return view


def test_line_missed_by_the_ocr_keeps_its_translation():
    both = [("Jake", "ngl this game is mid", 200), ("Luc", "mdr jsp comment on fait", 222)]
    view = translated_view(("Jake", "ngl this game is mid"), ("Luc", "mdr jsp comment on fait"))
    view.render(chat_frame(both), True)
    assert len(view.layer.visible) == 2
    # En esta captura el OCR no leyó la línea de Jake (el chat no se movió y su texto sigue ahí): no parpadea.
    view.render(chat_frame(both[1:], on_screen=both), True)
    assert len(view.layer.visible) == 2
    # Si su texto ya no está (se cerró el chat, se borró el mensaje), su traducción se va.
    view.render(chat_frame(both[1:], on_screen=both[1:]), True)
    assert len(view.layer.visible) == 1


def test_burst_of_new_messages_is_not_taken_as_scrolling():
    names = ["Jake", "Luc", "Mia", "Memo", "Kai", "Ana"]
    view = translated_view(*[(name, f"mensaje de {name} numero {i}") for i, name in enumerate(names)])
    lines: list[tuple[str, str]] = []
    for i, name in enumerate(names):  # llega uno por captura: todo sube una línea cada vez
        lines.append((name, f"mensaje de {name} numero {i}"))
        view.render(chat_frame([(who, text, 250 - 22 * (len(lines) - 1 - k)) for k, (who, text) in enumerate(lines)]),
                    True)
        assert len(view.layer.visible) == len(lines)
    assert view.layer.hidden_all == 0


def chat_image(lines: list[str], bottom: int = 230) -> Image.Image:
    image = Image.new("RGB", (400, 240), (30, 34, 44))
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("arialbd.ttf", 15)
    for i, line in enumerate(reversed(lines)):
        draw.text((8, bottom - 22 * (i + 1)), line, font=font, fill=(255, 255, 255), stroke_width=1, stroke_fill=(0, 0, 0))
    return image


def test_shift_of_the_chat_is_estimated_without_ocr():
    lines = ["Jake: brb gotta eat", "Luana: me ajuda no obby pfv", "Luc: mdr jsp comment on fait",
             "Juan: alguien juega obby?", "Mia: anyone wanna trade my dragon?", "Memo: esperen que me conecto",
             "cloverx3: news nahi dekh rhi", "Pedro_BR: vlw mano, tmj"]
    before = prepare_chat_image(chat_image(lines))
    after = prepare_chat_image(chat_image(lines[1:] + ["Martina: sos re malo jaja"]))
    assert estimate_shift(before, after) == -22  # llegó un mensaje: todo subió una línea
    assert estimate_shift(before, before) == 0


def test_translations_move_with_the_chat_right_away():
    view = translated_view(("Jake", "ngl this game is mid"))
    view.render(chat_frame([("Jake", "ngl this game is mid", 200)]), True)
    (x, y), = view.layer.visible.values()
    view.shift(-22)  # el chat subió una línea: la traducción va con el mensaje, sin esperar el OCR
    assert list(view.layer.visible.values()) == [(x, y - 22)]
    view.shift(-300)  # se fue del chat
    assert view.layer.visible == {}


def test_same_message_twice_gets_two_pills():
    view = translated_view(("n7r0pyy", "Plss donate"))
    twice = [("n7r0pyy", "Plss donate", 200), ("n7r0pyy", "Plss donate", 222)]
    view.render(chat_frame(twice), True)
    assert len(view.layer.visible) == 2
    view.render(chat_frame(twice), True)
    assert len(view.layer.visible) == 2 and len(set(view.layer.visible.values())) == 2


def test_small_ocr_jitter_does_not_move_the_translation():
    view = translated_view(("Jake", "ngl this game is mid"))
    view.render(chat_frame([("Jake", "ngl this game is mid", 200)]), True)
    first = dict(view.layer.visible)
    view.render(chat_frame([("Jake", "ngl this game is mid", 201.5)]), True)
    assert view.layer.visible == first


def test_find_bubbles_in_synthetic_scene():
    scene = Image.new("RGB", (600, 300), (60, 120, 60))
    draw = ImageDraw.Draw(scene)
    draw.rounded_rectangle((200, 60, 420, 110), 12, fill=(250, 250, 250))
    font = ImageFont.truetype("arial.ttf", 18)
    draw.text((215, 74), "wanna trade?", font=font, fill=(40, 40, 40))
    draw.text((20, 250), "Cartel del juego", font=font, fill=(255, 255, 255))  # texto claro sobre el juego
    bubble_row = OcrRow("wanna trade?", 74, 20, 215, 120)
    sign_row = OcrRow("Cartel del juego", 250, 20, 20, 150)
    bubbles = find_bubbles(scene, [bubble_row, sign_row])
    assert [b.text for b in bubbles] == ["wanna trade?"]
    assert bubbles[0].background[0] > 230 and bubbles[0].foreground[0] < 90


def test_short_translation_is_spread_over_every_row_of_the_message():
    slots = [Slot(0, 0, 300, 20), Slot(0, 20, 300, 40)]
    size, lines = layout_text("alguém sabe onde fica o boss", slots, 16, balance=True)
    assert all(lines) and " ".join(lines) == "alguém sabe onde fica o boss"  # ninguna línea vacía
    size, lines = layout_text("alguém sabe onde fica o boss", slots, 16)
    assert lines[1] == ""  # sin repartir (los subtítulos): una sola línea


def test_ocr_garbage_is_never_announced():
    from bubble.capture.chat_parser import ChatTracker, looks_garbled
    from bubble.translate.base import ChatLine

    assert looks_garbled("fdr.jiPCgmôtTt on-áit") and looks_garbled(": Ples dom.te")
    assert not any(looks_garbled(t) for t in ("mdr jsp comment on fait", "kkkkkkk", "wkwkwk", "LOL xD", "gg ez"))
    tracker = ChatTracker()
    tracker.update([ChatLine("Juan", "hola")])
    assert tracker.update([ChatLine("Juan", "hola"), ChatLine("tuc", "fdr.jiPCgmôtTt on-áit")]) == []
    assert tracker.update([ChatLine("Juan", "hola"), ChatLine("tuc", "fdr.jiPCgmôtTt on-áit")]) == []
    assert tracker.update([ChatLine("Juan", "hola"), ChatLine("Luc", "mdr jsp comment on fait")]) == [
        ChatLine("Luc", "mdr jsp comment on fait")]

