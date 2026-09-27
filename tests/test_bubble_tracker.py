import time

from PIL import Image, ImageDraw, ImageFont

from bubble.capture.bubble_tracker import (
    SIGNATURE_CHANGED, SIGNATURE_OTHER, BubbleTracker, find_bubble_boxes, signature_distance,
)
from bubble.geometry import Rect


def scene(offset_x: int = 0, offset_y: int = 0, lines=("bhai kidher hai tu", "isse baat kro"),
          extra=None, scale: float = 1.0) -> Image.Image:
    """Escena tipo Roblox 1920x1080: cielo, pasto, una pared blanca sin texto y una burbuja con colita."""
    image = Image.new("RGB", (1920, 1080), (120, 170, 230))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 540, 1920, 1080), fill=(70, 130, 60))
    draw.rectangle((1500, 600, 1700, 700), fill=(245, 245, 245))  # pared blanca, sin letras
    bubbles = [((800 + offset_x, 380 + offset_y), lines)]
    if extra:
        bubbles.append(extra)
    font = ImageFont.truetype("arial.ttf", int(22 * scale))
    for (x, y), text_lines in bubbles:
        width, height = int(300 * scale), int((20 + 30 * len(text_lines)) * scale)
        draw.rounded_rectangle((x, y, x + width, y + height), 14, fill=(250, 250, 250))
        draw.polygon([(x + width / 2 - 10, y + height), (x + width / 2 + 10, y + height),
                      (x + width / 2, y + height + 12)], fill=(250, 250, 250))  # colita
        for i, line in enumerate(text_lines):
            draw.text((x + 18 * scale, y + (10 + 30 * i) * scale), line, font=font, fill=(45, 45, 45))
    return image


def test_finds_only_the_bubble_body_and_fast_enough():
    image = scene()
    find_bubble_boxes(image)
    start = time.perf_counter()
    boxes = find_bubble_boxes(image)
    elapsed = time.perf_counter() - start
    assert len(boxes) == 1
    box = boxes[0]
    assert abs(box.left - 800) <= 6 and abs(box.top - 380) <= 6 and abs(box.width - 300) <= 12
    assert abs(box.bottom - 460) <= 6  # sin la colita
    assert box.background[0] > 235 and box.foreground[0] < 90
    assert elapsed < 0.15, f"demasiado lento: {elapsed:.3f}s"


def test_signature_same_text_vs_new_message():
    base = find_bubble_boxes(scene())[0].signature
    moved = find_bubble_boxes(scene(offset_x=-120, offset_y=30))[0].signature
    bigger = find_bubble_boxes(scene(scale=1.25))[0].signature
    smaller = find_bubble_boxes(scene(scale=0.8))[0].signature
    other = find_bubble_boxes(scene(lines=("wanna trade my dragon", "for your pet?")))[0].signature
    almost = find_bubble_boxes(scene(lines=("bhai kidher hai", "isse baat kro")))[0].signature
    assert signature_distance(base, moved) < 0.01  # la huella se ancla a las letras: moverse no la cambia
    assert signature_distance(base, bigger) < SIGNATURE_CHANGED  # acercarse o alejarse tampoco
    assert signature_distance(base, smaller) < SIGNATURE_CHANGED
    assert signature_distance(base, other) > 0.15
    assert signature_distance(base, almost) > SIGNATURE_OTHER  # casi el mismo texto: igual es otro mensaje


def test_tracker_follows_bubble_when_camera_moves():
    now = [0.0]
    tracker = BubbleTracker(clock=lambda: now[0])
    first = tracker.update(find_bubble_boxes(scene()))
    assert len(first) == 1
    first[0].text = "bhai kidher hai tu isse baat kro"
    first[0].ocr_signature = first[0].box.signature
    for step in range(1, 6):  # la cámara gira: la burbuja se corre 60 px por captura
        now[0] = step * 0.12
        tracks = tracker.update(find_bubble_boxes(scene(offset_x=-60 * step, offset_y=10 * step)))
        assert len(tracks) == 1 and tracks[0].id == first[0].id  # es la misma burbuja: no se vuelve a leer
        assert not tracks[0].needs_ocr()
        assert abs(tracks[0].box.left - (800 - 60 * step)) <= 6
    now[0] = 2.0  # desapareció hace rato: se olvida
    assert tracker.update([]) == [] and tracker.tracks == []


def test_new_message_replaces_old_translation():
    now = [0.0]
    tracker = BubbleTracker(clock=lambda: now[0])
    old = tracker.update(find_bubble_boxes(scene()))[0]
    old.text, old.ocr_signature = "bhai kidher hai tu isse baat kro", old.box.signature
    # El jugador escribe otra vez en el mismo lugar: la burbuja tiene otro texto.
    now[0] = 0.12
    same_place = tracker.update(find_bubble_boxes(scene(lines=("wanna trade my dragon", "for your pet?"))))
    assert len(same_place) == 1
    assert same_place[0].current_text == ""  # no se muestra la traducción del mensaje anterior
    assert same_place[0].needs_ocr()


def test_bubble_behind_the_chat_keeps_its_text_and_full_size():
    now = [0.0]
    tracker = BubbleTracker(clock=lambda: now[0])
    chat = Rect(0, 300, 700, 250)  # el panel del chat termina en x = 700
    track = tracker.update(find_bubble_boxes(scene(), chat))[0]
    track.text, track.ocr_signature = "bhai kidher hai tu isse baat kro", track.box.signature
    assert not track.clipped
    # La cámara gira y la burbuja pasa por detrás del chat: solo se ve su parte derecha.
    image = scene(offset_x=-200)
    ImageDraw.Draw(image).rectangle((0, 300, 700, 550), fill=(28, 30, 36))
    now[0] = 0.12
    tracks = tracker.update(find_bubble_boxes(image, chat))
    assert len(tracks) == 1 and tracks[0].id == track.id
    hidden = tracks[0]
    assert hidden.clipped and hidden.visible.width < 230
    assert abs(hidden.box.width - track.box.width) <= 2 and abs(hidden.box.left - 600) <= 8  # la burbuja entera
    assert hidden.current_text == track.text and not hidden.needs_ocr()  # ni desaparece ni se relee a medias


def test_something_bright_that_is_not_a_bubble_is_not_read_every_frame():
    now = [0.0]
    tracker = BubbleTracker(clock=lambda: now[0])
    track = tracker.update(find_bubble_boxes(scene()))[0]
    assert track.needs_ocr(now[0])
    track.read_at, track.ocr_signature = now[0], track.box.signature  # se leyó y no tenía texto
    now[0] = 0.12
    again = tracker.update(find_bubble_boxes(scene()))[0]
    assert not again.needs_ocr(now[0])
    now[0] = 0.24
    assert again.needs_ocr(now[0] + 5)  # de a ratos sí se reintenta


def test_old_bubble_moves_up_and_new_one_appears_below():
    now = [0.0]
    tracker = BubbleTracker(clock=lambda: now[0])
    old = tracker.update(find_bubble_boxes(scene()))[0]
    old.text, old.ocr_signature = "bhai kidher hai tu isse baat kro", old.box.signature
    now[0] = 0.12
    # La burbuja vieja sube 100 px y aparece la nueva donde estaba la vieja.
    image = scene(offset_y=-100, extra=((800, 380), ("wanna trade my dragon", "for your pet?")))
    tracks = tracker.update(find_bubble_boxes(image))
    assert len(tracks) == 2
    kept = next(t for t in tracks if t.id == old.id)
    new = next(t for t in tracks if t.id != old.id)
    assert kept.box.top < new.box.top  # la vieja (arriba) conserva su traducción
    assert kept.current_text and not new.text and new.needs_ocr()


def test_fast_closing_matches_scipy():
    import numpy as np

    from bubble.capture.bubble_tracker import _ndimage, close_3x3

    rng = np.random.default_rng(3)
    for density in (0.3, 0.6, 0.9):
        mask = rng.random((61, 97)) < density
        assert np.array_equal(close_3x3(mask), _ndimage().binary_closing(mask, structure=np.ones((3, 3))))



# Lecturas reales del OCR sobre una grabación de Roblox (una burbuja larga que quedaba tapada en parte).
FULL = ("Once upon a time, there was a beautiful young princess named Snow White. She lived in a faraway kingdom "
        "with her father and stepmother.")
PARTIAL_READS = [
    "upon a time, there was a beautiful princess named Snow White. She in a faraway kingdom with her father and "
    "stepmother.",
    "-e upon a time, there was a beautiful Ing princess named Snow White. She ved in a faraway kingdom with her "
    "father and stepmother.",
    "pon a time, there was a beautiful princess named Snow White. She in a faraway kingdom with her father and "
    "stepmother.",
]


def test_partial_reads_never_replace_the_whole_bubble():
    from bubble.capture.bubble_tracker import BubbleTexts

    texts = BubbleTexts()
    text, rows = texts.settle("", 1, FULL, 4, now=0.0)
    for index, read in enumerate(PARTIAL_READS):
        text, rows = texts.settle(text, rows, read, 4, now=1.0 + index)
        assert text == FULL, read
    # Otra pista (la misma burbuja vista de nuevo, tapada): toma el texto entero que se leyó hace poco.
    assert texts.settle("", 1, "e upon a time, there was", 1, now=6.0)[0] == FULL
    # Un mensaje nuevo en esa burbuja sí la reemplaza, y uno más largo que el anterior también.
    assert texts.settle(FULL, 4, "Mirror mirror on the wall", 1, now=7.0)[0] == "Mirror mirror on the wall"
    short = "The mirror would answer,"
    longer = 'The mirror would answer, "You are the most beautiful of all women."'
    assert texts.settle(short, 1, longer, 2, now=8.0)[0] == longer


def test_things_that_are_not_bubbles_are_ignored():
    from bubble.capture.bubble_tracker import usable_bubble_text

    for junk in ("clil", "elil", "c(ll", "rill", "$333 (06S4)", "$333 (05S2)", "GRO (+3", "I GRO", "GR04", "IGNO (+3,",
                 "GRO("):
        assert not usable_bubble_text(junk), junk
    for message in ("Lucha de Brazo", "hi", "gg", "sus", "lol", "ok", "who wants to trade?", "hola",
                    'The mirror would answer, "You are the most beautiful of all women."'):
        assert usable_bubble_text(message), message
