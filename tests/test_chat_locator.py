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
        row("Jake", 400, left=640, width=40),  # nombre sobre un jugador
        row("SHOP", 60, left=900, width=60),  # cartel del juego
        row("Level: 12", 40, left=1700, width=90),  # HUD del juego ("Nombre: valor"), no chat
    ]
    guess = find_chat_region(rows, 1920, 1080)
    assert guess is not None and guess.lines == 5
    region = guess.region
    assert region.left <= 20 and region.width >= MIN_WIDTH_SHARE * 1920 - 1  # ancho suficiente para mensajes largos
    assert region.width <= 0.4 * 1920  # sin superar mucho el ancho del chat (las traducciones se desbordaban)
    assert region.bottom >= 288 + 16 + 22  # una línea de margen abajo, donde aparecen los mensajes nuevos
    assert region.height >= 9 * 22  # espacio para unas 10 líneas (el chat sube con cada mensaje)
    assert region.right < 1600  # no se extiende hasta el HUD


def test_no_chat_when_there_is_no_message_like_text():
    rows = [row("SHOP", 60, left=900, width=60), row("Jake", 400, left=640, width=40)]
    assert find_chat_region(rows, 1920, 1080) is None


def test_a_saved_chat_zone_as_wide_as_the_window_is_redone():
    from bubble.geometry import Rect
    from bubble.roblox import REGION_VERSION, region_outdated

    client = Rect(0, 0, 1920, 1050)
    zone = {"relative": True, "x": 0, "y": 137, "w": 652, "h": 279}
    # la zona guardada abarca casi todo el ancho
    assert region_outdated({**zone, "w": 1433, "detector": REGION_VERSION}, client)
    assert region_outdated(zone, client)  # zona de una versión anterior (sin registro de origen): se detecta de nuevo
    assert region_outdated({**zone, "detector": REGION_VERSION - 1}, client)
    assert not region_outdated({**zone, "detector": REGION_VERSION}, client)
    assert not region_outdated({**zone, "manual": True}, client)  # zona definida manualmente: se respeta
