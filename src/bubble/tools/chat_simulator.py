"""Simulador de Roblox para probar Bubble sin el juego.

Dibuja con PIL (a unos 20 cuadros por segundo) una escena con cámara giratoria, un chat semitransparente al estilo de
TextChatService (banderas, nombres de colores, texto blanco con borde, mensajes largos en varias líneas, avisos
[SYSTEM], spam y la barra "To chat click here…") y jugadores con burbujas de chat apiladas (la nueva abajo, la anterior
arriba).

Cada mensaje o burbuja que aparece se registra en un archivo JSON con la hora exacta, para medir cuánto tarda Bubble en
detectarlo y traducirlo.

Uso:  python -m bubble.tools.chat_simulator <escenario> <registro.jsonl> Escenarios: lento, medio, rapido, rafagas
"""

from __future__ import annotations

import ctypes
import json
import math
import random
import sys
import time
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageTk

WIDTH, HEIGHT = 1100, 650
CHAT = (10, 64, 500, 300)  # zona de mensajes (x0, y0, x1, y1) en la ventana
CHAT_REGION = {"relative": True, "x": 10, "y": 64, "w": 490, "h": 236}  # para calibrar Bubble con la misma región
LINE_HEIGHT = 22
TEXT_WIDTH = CHAT[2] - CHAT[0] - 18
TITLE = "FakeRoblox"

SPEAKERS = {  # nombre: (idioma, color del nombre, bandera)
    "Pedro_BR": ("pt", (120, 200, 255), ((0, 156, 59), (255, 223, 0))),
    "Luana": ("pt", (255, 150, 200), ((0, 156, 59), (255, 223, 0))),
    "Jake": ("en", (255, 120, 120), ((60, 60, 160), (220, 60, 60))),
    "Mia": ("en", (200, 160, 255), ((60, 60, 160), (220, 60, 60))),
    "cloverx3": ("hi", (140, 230, 140), ((255, 153, 51), (19, 136, 8))),
    "anonadhi": ("hi", (255, 110, 110), ((255, 153, 51), (19, 136, 8))),
    "Juan": ("es", (230, 200, 90), ((116, 172, 223), (255, 255, 255))),
    "Martina": ("es", (255, 180, 90), ((116, 172, 223), (255, 255, 255))),
    "Memo": ("es", (120, 220, 200), ((0, 104, 71), (206, 17, 38))),
    "Luc": ("fr", (170, 190, 255), ((0, 85, 164), (239, 65, 53))),
    "n7r0pyy": ("en", (230, 200, 70), None),
}
MESSAGES = {
    "es": ["hola che, todo bien?", "alguien juega obby?", "jajaja no puede ser", "dale, vamos a la base",
           "quién tiene robux?", "sos re malo jaja", "esperen que me conecto", "vamos al lobby", "q onda gente",
           "buenas, de dónde son?", "no mames wey, neta?", "órale vamos"],
    "pt": ["vlw mano, tmj", "alguém quer trocar pet?", "kkkkkk", "mds que lag", "bora pra base",
           "vc é muito noob kkk", "quem tem robux?", "slk esse jogo é top", "me ajuda no obby pfv",
           "alguém sabe onde fica o boss? to perdido faz uns dez minutos aqui"],
    "en": ["anyone wanna trade my dragon?", "ngl this game is mid", "bro stop spawn killing me",
           "who wants to team up?", "can someone carry me pls", "i have a shadow dragon ft",
           "brb gotta eat", "omg i got a legendary!!",
           "Once upon a time there was a little girl who wore a beautiful red cloak and everyone loved her"],
    "hi": ["bhai kidher hai tu", "isse baat kro", "abey chup", "news nahi dekh rhi", "aaj kal kya chal rha yaar",
           "follow kro yaar"],
    "fr": ["mdr jsp comment on fait", "tkt frr", "qui veut échanger ?"],
}
SYSTEM = ["jody jo donated 10 to crisxlives!", "Insaan has added a comment to danger! (+25)",
          "Kira joined the game", "yappy has added a comment to Suratt_xyzzz! (+25) \"good morning\""]
SPAM = ("n7r0pyy", ["Plss donate", "Plss donate", "Plsss donate", "Plss donatee"])
BUBBLE_PLAYERS = {"Jake": (620, 430), "Pedro_BR": (860, 470)}  # posición en la escena (sigue a la cámara)


@dataclass
class Message:
    speaker: str
    text: str
    lang: str
    kind: str = "player"  # player | system | spam
    lines: list[list[tuple[str, tuple]]] = field(default_factory=list)  # segmentos (texto, color) por línea


def build_scenario(name: str, seed: int = 7) -> list[tuple[float, str, str, str, str]]:
    """Lista de (segundo, nombre, texto, idioma, tipo)."""
    rng = random.Random(seed)
    speakers = [s for s in SPEAKERS if s != "n7r0pyy"]

    def player_message() -> tuple[str, str, str, str]:
        speaker = rng.choice(speakers)
        lang = SPEAKERS[speaker][0]
        return speaker, rng.choice(MESSAGES[lang]), lang, "player"

    events: list[tuple[float, str, str, str, str]] = []
    t = 0.0
    if name == "rafagas":
        for burst in range(3):
            base = burst * 9.0
            for i in range(8):
                events.append((base + i * rng.uniform(0.12, 0.25), *player_message()))
        events.append((5.0, "SYSTEM", rng.choice(SYSTEM), "en", "system"))
        return sorted(events)
    count, low, high = {"lento": (16, 3.5, 5.0), "medio": (30, 1.2, 2.0), "rapido": (40, 0.4, 0.9)}[name]
    spam_at = count // 2
    for i in range(count):
        t += rng.uniform(low, high)
        if i == spam_at:  # un jugador repite el mismo pedido
            for j, text in enumerate(SPAM[1]):
                events.append((t + j * 0.5, SPAM[0], text, "en", "spam" if j >= 2 else "player"))
            t += 2.0
        elif i % 7 == 3:
            events.append((t, "SYSTEM", rng.choice(SYSTEM), "en", "system"))
        else:
            events.append((t, *player_message()))
    return events


class Simulator:
    def __init__(self, scenario: str, log_path: Path, fade: bool = True) -> None:
        # "<escenario>_transparente": el fondo del chat no reaparece con los mensajes nuevos (el caso más difícil).
        self.always_faded = scenario.endswith("_transparente")
        self.events = build_scenario(scenario.removesuffix("_transparente"))
        self.fade = fade  # el fondo del chat se desvanece sin mensajes nuevos, como en Roblox
        self.last_activity = time.time()
        self.log = log_path.open("w", encoding="utf-8")
        self.font = ImageFont.truetype("arialbd.ttf", 15)
        self.bubble_font = ImageFont.truetype("arial.ttf", 16)
        self.messages: list[Message] = []
        self.bubbles: dict[str, list[tuple[str, float]]] = {name: [] for name in BUBBLE_PLAYERS}
        self.start = 0.0
        self.next_event = 0
        self.root = tk.Tk()
        self.root.title(TITLE)
        self.root.geometry(f"{WIDTH}x{HEIGHT}+80+60")
        self.root.resizable(False, False)
        self.label = tk.Label(self.root, bd=0)
        self.label.pack()
        # Barra del chat como la de Roblox: se abre con la tecla física "/" (VK_OEM_2) y Enter envía. Cualquier otra
        # tecla con el chat cerrado se considera dirigida al juego y se registra (Bubble no debería procesar ninguna).
        self.typing: str | None = None
        self.root.bind("<KeyPress>", self._on_key)
        # Chat existente al ingresar al servidor (Bubble no debería traducirlo todo).
        for speaker, text in [("Juan", "buenas gente"), ("Jake", "gg"), ("Luana", "oi galera")]:
            self._add(Message(speaker, text, SPEAKERS[speaker][0]), log=False)

    # ---------------------------------------------------------------- mensajes
    def _wrap(self, message: Message) -> None:
        """Arma las líneas como el chat de Roblox: "[bandera] Nombre: texto", con el texto sobrante en las líneas
        siguientes.
        """
        name_color = (255, 220, 90) if message.kind == "system" else SPEAKERS.get(message.speaker, ("", (220, 220, 220)))[1]
        prefix = "[SYSTEM]: " if message.kind == "system" else f"{message.speaker}: "
        words = message.text.split()
        lines: list[list[tuple[str, tuple]]] = []
        current: list[tuple[str, tuple]] = [(prefix, name_color)]
        width = self.font.getlength(prefix) + (28 if self._flag(message) else 0)
        text = ""
        for word in words:
            candidate = f"{text} {word}".strip()
            if width + self.font.getlength(candidate) > TEXT_WIDTH and text:
                current.append((text, (255, 255, 255)))
                lines.append(current)
                current, width, text = [], 0, word
            else:
                text = candidate
        current.append((text, (255, 255, 255)))
        lines.append(current)
        message.lines = lines

    @staticmethod
    def _flag(message: Message):
        return None if message.kind == "system" else SPEAKERS.get(message.speaker, (None, None, None))[2]

    def _add(self, message: Message, log: bool = True, event: str | None = None) -> None:
        self._wrap(message)
        self.messages.append(message)
        self.last_activity = time.time()
        self.messages = self.messages[-40:]
        if message.speaker in BUBBLE_PLAYERS and message.kind == "player":
            stack = self.bubbles[message.speaker]
            stack.append((message.text, time.time()))
            del stack[:-3]
        # Los mensajes presentes al ingresar se registran aparte: Bubble puede traducir los últimos, por lo que no son
        # ruido.
        self._write(event or ("message" if log else "initial"), speaker=message.speaker, text=message.text,
                    lang=message.lang, kind=message.kind,
                    bubble=log and message.speaker in BUBBLE_PLAYERS and message.kind == "player")

    def _write(self, event_name: str, **data) -> None:
        self.log.write(json.dumps({"t": time.time(), "event": event_name, **data}, ensure_ascii=False) + "\n")
        self.log.flush()

    # ---------------------------------------------------------------- teclado (barra del chat)
    MODIFIER_KEYS = {"Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R", "Win_L", "Win_R"}

    def _on_key(self, event) -> None:
        if self.typing is None:
            if event.keycode == 0xBF:  # tecla física "/": abre el chat, como en Roblox
                # Como en Roblox: si la tecla produce un carácter distinto de "/" en este teclado (en español, "}"), ese
                # carácter queda escrito en la barra recién abierta.
                self.typing = event.char if event.char and event.char != "/" and event.char.isprintable() else ""
                self._write("abrir_chat")
            else:
                self._write("tecla_juego", keysym=event.keysym, keycode=event.keycode)
            return
        if event.keysym in ("Return", "KP_Enter"):
            text = self.typing.strip()
            self.typing = None
            if text:
                # el mensaje enviado aparece en el chat
                self._add(Message("Vos", text, "?"), log=False, event="enviado")
        elif event.keysym == "Escape":
            self.typing = None
        elif event.keysym == "BackSpace":
            self.typing = self.typing[:-1]
        elif event.keysym in self.MODIFIER_KEYS:
            self._write("tecla_extra", keysym=event.keysym)  # Ctrl, Shift, etc. mientras se escribe
        elif event.char and event.char.isprintable():
            self.typing += event.char

    # ---------------------------------------------------------------- dibujo
    def _camera(self, now: float) -> tuple[int, int]:
        elapsed = now - self.start
        return int(90 * math.sin(elapsed * 0.7)), int(14 * math.sin(elapsed * 0.45))

    def _scene(self, ox: int, oy: int) -> Image.Image:
        image = Image.new("RGB", (WIDTH, HEIGHT), (118, 170, 228))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 330 + oy, WIDTH, HEIGHT), fill=(72, 132, 62))
        for i in range(-2, 12):  # árboles y casas que pasan con la cámara
            x = i * 140 + ox
            draw.rectangle((x, 250 + oy, x + 24, 340 + oy), fill=(110, 80, 50))
            draw.ellipse((x - 40, 180 + oy, x + 64, 270 + oy), fill=(46, 110, 50))
            if i % 3 == 0:
                draw.rectangle((x + 60, 280 + oy, x + 130, 350 + oy), fill=(220, 210, 190))
        for name, (px, py) in BUBBLE_PLAYERS.items():
            x, y = px + ox, py + oy
            draw.ellipse((x - 22, y - 60, x + 22, y - 16), fill=(245, 200, 150))
            draw.rectangle((x - 25, y - 16, x + 25, y + 45), fill=(40, 60, 160) if name == "Jake" else (40, 140, 60))
            draw.text((x, y - 78), name, font=self.font, fill=(255, 255, 255), anchor="mm", stroke_width=1,
                      stroke_fill=(0, 0, 0))
        return image

    def _panel_brightness(self, now: float) -> float:
        """Como en Roblox: el fondo oscuro del chat se desvanece a los pocos segundos sin mensajes nuevos (el texto
        queda sobre el juego, que se mueve con la cámara) y reaparece al llegar uno o al escribir.
        """
        quiet = now - getattr(self, "last_activity", now)
        if getattr(self, "typing", None) is not None:
            return 0.32
        if getattr(self, "always_faded", False):
            return 1.0
        if quiet < 4.0:
            return 0.32
        return 0.32 + (1.0 - 0.32) * min(1.0, (quiet - 4.0) / 0.8)

    def _draw_chat(self, image: Image.Image, brightness: float = 0.32) -> None:
        x0, y0, x1, y1 = CHAT
        if brightness < 0.999:
            panel = image.crop((x0 - 4, y0 - 34, x1, y1 + 40))
            panel = ImageEnhance.Brightness(panel).enhance(brightness)  # fondo oscuro semitransparente del chat
            image.paste(panel, (x0 - 4, y0 - 34))
        draw = ImageDraw.Draw(image)
        for i, tab in enumerate(["Here", "Global", "Friends"]):
            draw.text((x0 + 60 + i * 150, y0 - 18), tab, font=self.font, fill=(230, 230, 230), anchor="mm")
        # Texto con borde negro semitransparente (como el chat de Roblox, TextStrokeTransparency 0.5): sobre fondo claro
        # el borde se ve gris, no negro.
        strokes = Image.new("RGBA", image.size, (0, 0, 0, 0))
        stroke_draw = ImageDraw.Draw(strokes)
        fills: list[tuple[tuple[int, int], str, tuple]] = []

        def text_with_stroke(x: float, y: int, text: str, color: tuple) -> None:
            stroke_draw.text((x, y), text, font=self.font, fill=(0, 0, 0, 128), stroke_width=1, stroke_fill=(0, 0, 0, 128))
            fills.append(((x, y), text, color))

        rows = [(message, line) for message in self.messages for line in message.lines][-10:]
        y = y1 - len(rows) * LINE_HEIGHT
        for message, line in rows:
            x = x0 + 6
            if line is message.lines[0] and self._flag(message):
                colors = self._flag(message)
                text_with_stroke(x, y, "[", (230, 230, 230))
                draw.rectangle((x + 7, y + 4, x + 21, y + 8), fill=colors[0])
                draw.rectangle((x + 7, y + 9, x + 21, y + 13), fill=colors[1])
                text_with_stroke(x + 22, y, "]", (230, 230, 230))
                x += 30
            for text, color in line:
                text_with_stroke(x, y, text, color)
                x += self.font.getlength(text)
            y += LINE_HEIGHT
        image.paste(strokes, (0, 0), strokes)
        draw = ImageDraw.Draw(image)
        for position, text, color in fills:
            draw.text(position, text, font=self.font, fill=color)
        if getattr(self, "typing", None) is None:
            draw.rounded_rectangle((x0 + 4, y1 + 6, x1 - 6, y1 + 34), 12, fill=(60, 60, 64))
            draw.text((x0 + 20, y1 + 20), "To chat click here or press / key", font=self.font, fill=(150, 150, 150),
                      anchor="lm")
        else:
            draw.rounded_rectangle((x0 + 4, y1 + 6, x1 - 6, y1 + 34), 12, fill=(80, 80, 86), outline=(230, 230, 230))
            draw.text((x0 + 20, y1 + 20), self.typing + "|", font=self.font, fill=(255, 255, 255), anchor="lm")

    def _draw_bubbles(self, image: Image.Image, now: float, ox: int, oy: int) -> None:
        draw = ImageDraw.Draw(image)
        for name, stack in self.bubbles.items():
            stack[:] = [(text, t) for text, t in stack if now - t < 12]  # las burbujas desaparecen a los 12 s
            px, py = BUBBLE_PLAYERS[name]
            bottom = py + oy - 95
            for text, _t in reversed(stack):  # la más nueva abajo; las anteriores suben
                width = min(260, int(self.bubble_font.getlength(text)) + 28)
                lines = self._bubble_lines(text, width - 24)
                height = 14 + 22 * len(lines)
                x = px + ox - width // 2
                draw.rounded_rectangle((x, bottom - height, x + width, bottom), 12, fill=(250, 250, 250))
                draw.polygon([(px + ox - 8, bottom), (px + ox + 8, bottom), (px + ox, bottom + 9)], fill=(250, 250, 250))
                for i, line in enumerate(lines):
                    draw.text((px + ox, bottom - height + 18 + 22 * i), line, font=self.bubble_font,
                              fill=(40, 42, 46), anchor="mm")
                bottom -= height + 14

    def _bubble_lines(self, text: str, width: int) -> list[str]:
        lines, current = [], ""
        for word in text.split():
            candidate = f"{current} {word}".strip()
            if self.bubble_font.getlength(candidate) > width and current:
                lines.append(current)
                current = word
            else:
                current = candidate
        return lines + [current]

    # ---------------------------------------------------------------- bucle
    def _tick(self) -> None:
        now = time.time()
        while self.next_event < len(self.events) and now - self.start >= self.events[self.next_event][0]:
            _t, speaker, text, lang, kind = self.events[self.next_event]
            self._add(Message(speaker, text, lang, "system" if kind == "system" else kind))
            self.next_event += 1
        ox, oy = self._camera(now)
        image = self._scene(ox, oy)
        self._draw_bubbles(image, now, ox, oy)
        self._draw_chat(image, self._panel_brightness(now) if self.fade else 0.32)
        self._photo = ImageTk.PhotoImage(image)
        self.label.configure(image=self._photo)
        if self.next_event >= len(self.events) and now - self.start > self.events[-1][0] + 8:
            self._write("end")
            self.root.destroy()
            return
        self.root.after(45, self._tick)

    def run(self, delay_s: float = 4.0) -> None:
        self.start = time.time() + delay_s
        self._write("start", events=len(self.events))
        self.root.after(45, self._tick)
        self.root.mainloop()


def main() -> None:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
    Simulator(sys.argv[1], Path(sys.argv[2])).run()


if __name__ == "__main__":
    main()
