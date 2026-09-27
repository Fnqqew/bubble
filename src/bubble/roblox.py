"""Integración externa con Roblox: ubicar el chat en pantalla y escribir en él."""

from __future__ import annotations

import time

from . import win32
from .geometry import Rect
from .state import load_state, update_state

ROBLOX_CHAT_LIMIT = 200


def load_chat_region() -> dict | None:
    return load_state().get("chat_region")


def save_chat_region(absolute: Rect, roblox_client: Rect | None) -> None:
    """Se guarda relativo a la ventana de Roblox, así sigue funcionando si la movés."""
    if roblox_client:
        region = {"relative": True, "x": absolute.left - roblox_client.left, "y": absolute.top - roblox_client.top,
                  "w": absolute.width, "h": absolute.height}
    else:
        region = {"relative": False, "x": absolute.left, "y": absolute.top, "w": absolute.width, "h": absolute.height}
    update_state(chat_region=region)


def resolve_chat_region(saved: dict | None, require_foreground: bool = False) -> Rect | None:
    """Región del chat en coordenadas de pantalla actuales.

    Con `require_foreground`, devuelve None si Roblox no es la ventana activa: si saliste del juego,
    en esa parte de la pantalla hay otra cosa y leerla generaba mensajes falsos.
    """
    if not saved:
        return None
    if require_foreground and not win32.roblox_is_foreground():
        return None
    rect = Rect(saved["x"], saved["y"], saved["w"], saved["h"])
    if not saved.get("relative"):
        return rect
    hwnd = win32.find_roblox_window()
    if hwnd is None:
        return None
    client = win32.client_rect(hwnd)
    return rect.offset(client.left, client.top)


def split_message(text: str, limit: int = ROBLOX_CHAT_LIMIT) -> list[str]:
    """Divide en partes de hasta `limit` caracteres sin cortar palabras."""
    text = " ".join(text.split())
    parts: list[str] = []
    while len(text) > limit:
        cut = text.rfind(" ", 0, limit + 1)
        if cut <= 0:
            cut = limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        parts.append(text)
    return parts


def combine_translations(pairs: list[tuple[str, str]], limit: int = ROBLOX_CHAT_LIMIT) -> list[str]:
    """Varias traducciones del mismo mensaje -> "[EN] hi | [PT] oi" en una línea, o una por idioma si no entra."""
    tagged = [f"[{lang.upper()}] {text}" for lang, text in pairs]
    if len(pairs) == 1:
        return [pairs[0][1]]
    combined = " | ".join(tagged)
    return [combined] if len(combined) <= limit else tagged


def open_chat(open_chat_key: str = "/") -> None:
    """Abre la barra del chat de Roblox con su tecla. "/" es la tecla física que Roblox escucha (sin Shift, en
    cualquier distribución de teclado)."""
    if open_chat_key in ("/", "slash", ""):
        win32.press_chat_key()
        if win32.chat_key_leaves_a_character():
            # Con un teclado en español esa tecla escribe "}": Roblox abre el chat y el carácter queda en la barra
            # (salía "}lol" en vez de "lol"). Se borra antes de escribir el mensaje.
            time.sleep(0.12)
            win32.press_backspace()
    else:
        win32.press_char(open_chat_key)


def send_messages(texts: list[str], hwnd: int, open_chat_key: str = "/", method: str = "type") -> None:
    for index, text in enumerate(texts):
        if index:
            time.sleep(1.2)  # Roblox limita mensajes muy seguidos
        send_to_chat(text, hwnd, open_chat_key, method)


class SendError(RuntimeError):
    pass


def send_to_chat(text: str, hwnd: int, open_chat_key: str = "/", method: str = "type") -> None:
    """Pone Roblox al frente, abre el chat, escribe el mensaje y lo envía. Nada más: ninguna otra tecla.

    Con `method="type"` (el normal) el texto se escribe como caracteres, sin Ctrl+V (que en algunos juegos hace
    algo) y sin tocar tu portapapeles. Bloquea ~0,3 s por mensaje.
    """
    parts = split_message(text)
    if not parts:
        return
    if not win32.force_foreground(hwnd):
        raise SendError("No se pudo poner Roblox en primer plano")
    time.sleep(0.08)
    win32.wait_modifiers_released()  # un Shift apretado se sumaría a la tecla del chat (y movería la cámara)
    previous_clipboard = None
    if method == "paste":
        try:
            previous_clipboard = win32.get_clipboard_text()
        except OSError:
            previous_clipboard = None
    try:
        for index, part in enumerate(parts):
            if index:
                time.sleep(1.2)  # Roblox limita mensajes muy seguidos
            open_chat(open_chat_key)
            time.sleep(0.15)  # que la barra del chat tome el foco
            if method == "paste":
                win32.set_clipboard_text(part)
                win32.press_keys(win32.VK_CONTROL, win32.VK_V)
            else:
                win32.type_unicode(part)
            time.sleep(0.06)
            win32.press_enter()
    finally:
        if previous_clipboard is not None:
            time.sleep(0.3)
            try:
                win32.set_clipboard_text(previous_clipboard)
            except OSError:
                pass
