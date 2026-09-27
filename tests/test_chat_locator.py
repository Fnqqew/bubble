from bubble.capture.chat_locator import MIN_WIDTH_SHARE, find_chat_region
from bubble.capture.ocr import OcrRow


def row(text: str, top: float, left: float = 20, width: float = 220) -> OcrRow:
    return OcrRow(text, top, 16, left, width)


def test_finds_the_chat_column_among_other_text_in_the_game():
    rows = [
        row("[■] Luc: mdr jsp comment on fait", 200),
        row("Juan: q onda gente", 222, left=48),
        row("[■] anonadhi: abey chup", 244),
        row("[SYSTEM]: Kira joined the game", 266),
        row("Jake: ngl this game is mid", 288, left=48),
        row("Jake", 400, left=640, width=40),  # nombre sobre la cabeza de un jugador
        row("SHOP", 60, left=900, width=60),  # un cartel del juego
        row("Level: 12", 40, left=1700, width=90),  # el HUD del juego ("Nombre: valor", pero solo)
    ]
    guess = find_chat_region(rows, 1920, 1080)
    assert guess is not None and guess.lines == 5
    region = guess.region
    assert region.left <= 20 and region.width >= MIN_WIDTH_SHARE * 1920 - 1  # ancho para mensajes largos
    assert region.width <= 0.4 * 1920  # pero no mucho más que el chat (las traducciones se salían)
    assert region.bottom >= 288 + 16 + 22  # una línea de margen abajo: ahí aparecen los mensajes nuevos
    assert region.height >= 9 * 22  # lugar para ~10 líneas (el chat sube a medida que llegan mensajes)
    assert region.right < 1600  # no se estira hasta el HUD


def test_no_chat_when_there_is_no_message_like_text():
    rows = [row("SHOP", 60, left=900, width=60), row("Jake", 400, left=640, width=40)]
    assert find_chat_region(rows, 1920, 1080) is None


def test_a_saved_chat_zone_as_wide_as_the_window_is_redone():
    from bubble.geometry import Rect
    from bubble.roblox import region_too_wide

    client = Rect(0, 0, 1920, 1050)
    assert region_too_wide({"relative": True, "x": 0, "y": 137, "w": 1433, "h": 279}, client)  # la que tenías
    assert not region_too_wide({"relative": True, "x": 0, "y": 137, "w": 652, "h": 279}, client)
