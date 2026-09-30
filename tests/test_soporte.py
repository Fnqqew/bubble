"""Soporte: arma el mensaje (título, descripción e imágenes), lo envía a un servidor de prueba local (nunca al real) y,
si el envío falla, lo deja listo para enviarlo manualmente.
"""

import http.server
import re
import threading
import time
import tkinter as tk

import pytest
from PIL import Image

from bubble import support


def report(tmp_path, images=0, **changes) -> support.Report:
    paths = []
    for index in range(images):
        path = tmp_path / f"captura{index}.png"
        Image.new("RGB", (800, 600), (40 * index, 90, 160)).save(path)
        paths.append(path)
    values = dict(kind="problema", title="La voz se corta", description="1. Abrí Bubble\n2. Hablé\n3. Se cortó",
                  contact="jugador@example.com", images=paths, system="Windows 11 · 16 GB", log="ERROR algo")
    values.update(changes)
    return support.Report(**values)


def parse(body: bytes, content_type: str):
    """Lee el formulario como lo haría un servidor (en UTF-8, como lo envían los navegadores)."""
    boundary = content_type.split("boundary=")[1].encode()
    fields, files = {}, {}
    for part in body.split(b"--" + boundary)[1:-1]:
        head, _, data = part.strip(b"\r\n").partition(b"\r\n\r\n")
        head = head.decode("utf-8")
        name = re.search(r'name="([^"]*)"', head).group(1)
        filename = re.search(r'filename="([^"]*)"', head)
        if filename:
            files[name] = (filename.group(1), data)
        else:
            fields[name] = data.decode("utf-8")
    return fields, files


def test_the_message_has_everything(tmp_path):
    message = report(tmp_path)
    assert message.subject() == "[Bubble · Problema] La voz se corta"
    body = message.body()
    assert body.startswith("1. Abrí Bubble")
    assert "Responder a: jugador@example.com" in body
    assert "— Su equipo —\nWindows 11" in body and "— Registro de errores (lo último) —\nERROR algo" in body
    bare = report(tmp_path, contact="", system="", log="").body()
    assert "Responder" not in bare and "Su equipo" not in bare


def test_big_screenshots_are_shrunk(tmp_path):
    path = tmp_path / "enorme.png"
    Image.effect_noise((3840, 2160), 60).convert("RGB").save(path)
    name, data = support.prepare_image(path)
    assert name == "enorme.jpg"
    with Image.open(__import__("io").BytesIO(data)) as image:
        assert max(image.size) == 1920 and image.format == "JPEG"
    assert len(data) < path.stat().st_size


class Collector(http.server.BaseHTTPRequestHandler):
    received: list = []
    answer = (200, "<h1>Thanks!</h1>")

    def do_POST(self):  # noqa: N802
        length = int(self.headers["Content-Length"])
        self.received.append((self.headers["Content-Type"], self.rfile.read(length)))
        status, page = self.answer
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(page.encode())

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    Collector.received = []
    Collector.answer = (200, "<h1>Thanks!</h1>")
    httpd = http.server.HTTPServer(("127.0.0.1", 0), Collector)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}/", Collector
    httpd.shutdown()


def test_sends_title_description_and_images(server, tmp_path):
    url, collector = server
    assert support.send(report(tmp_path, images=2), endpoint=url).startswith("Enviado.")
    fields, files = parse(collector.received[0][1], collector.received[0][0])
    assert fields["_subject"] == "[Bubble · Problema] La voz se corta"
    assert fields["Título"] == "La voz se corta" and fields["Tipo"] == "Problema"
    assert "3. Se cortó" in fields["Mensaje"] and "Windows 11" in fields["Mensaje"]
    assert fields["_replyto"] == "jugador@example.com" and fields["_captcha"] == "false"
    assert set(files) == {"imagen1", "imagen2"}
    # JPG válido (firma de bytes)
    assert files["imagen1"][0] == "captura0.jpg" and files["imagen1"][1][:2] == b"\xff\xd8"


def test_images_that_do_not_fit_are_left_out(server, tmp_path, monkeypatch):
    url, collector = server
    one = len(support.prepare_image(report(tmp_path, images=1).images[0])[1])
    monkeypatch.setattr(support, "MAX_BYTES", int(one * 2.5))
    support.send(report(tmp_path, images=4), endpoint=url)
    _fields, files = parse(collector.received[0][1], collector.received[0][0])
    assert len(files) == 2  # el mensaje se envía igual, con las imágenes disponibles


def test_first_message_asks_to_confirm_the_form(server, tmp_path):
    url, collector = server
    collector.answer = (200, "<p>Please check your inbox to activate this form</p>")
    assert "confirmar" in support.send(report(tmp_path), endpoint=url)


def test_a_server_error_is_reported(server, tmp_path):
    url, collector = server
    collector.answer = (500, "error")
    with pytest.raises(OSError):
        support.send(report(tmp_path), endpoint=url)


def test_without_internet_the_mail_is_ready_to_send_by_hand(tmp_path, monkeypatch):
    home = tmp_path / "usuario"
    (home / "Desktop").mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(home))
    opened = []
    monkeypatch.setattr(support.webbrowser, "open", opened.append)
    monkeypatch.setattr(support.os, "startfile", opened.append, raising=False)
    folder = support.fallback(report(tmp_path, images=1))
    assert folder == home / "Desktop" / "Bubble - soporte"
    assert "La voz se corta" in (folder / "mensaje.txt").read_text(encoding="utf-8")
    assert (folder / "captura0.png").exists()
    assert "to=juanmartindeza14%40gmail.com" in opened[0] and "su=%5BBubble" in opened[0]
    assert opened[1] == folder


# ---------------------------------------------------------------- la ventana
@pytest.fixture
def window(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from bubble.ui import widgets
    from bubble.ui.support_window import SupportWindow

    monkeypatch.setattr(widgets, "present", lambda window, root: None)  # oculta: nunca le quita el foco al jugador
    monkeypatch.setattr(support, "system_text", lambda: "PC de prueba")
    monkeypatch.setattr(support, "log_text", lambda: "")
    root = tk.Tk()
    root.withdraw()
    try:
        yield SupportWindow(root, grab_roblox=lambda: Image.new("RGB", (320, 200), "navy"))
    finally:
        root.destroy()


def pump(window, until, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end and not until():
        window.window.update()
        time.sleep(0.02)


def fill(window, title="La voz se corta", text="Apreté V y se cortó a la mitad"):
    window.title.event_generate("<FocusIn>")
    window.title.delete(0, "end")
    window.title.insert(0, title)
    window.text.event_generate("<FocusIn>")
    window._text_hint = False
    window.text.delete("1.0", "end")
    window.text.insert("1.0", text)


def test_needs_a_title_and_what_happened(window, monkeypatch):
    sent = []
    monkeypatch.setattr(support, "send", sent.append)
    window._send()  # vacío (solo los textos de ejemplo)
    assert not sent and "título" in str(window.status.cget("text"))
    fill(window, text="corto")
    window._send()
    assert not sent and "Contá un poco más" in str(window.status.cget("text"))


def test_sends_with_images_and_pc_data(window, monkeypatch):
    sent = []
    monkeypatch.setattr(support, "send", lambda message: sent.append(message) or "Enviado. Gracias por escribir.")
    fill(window)
    window._shot()  # «Captura de Roblox»
    assert len(window.images) == 1 and window._thumbs
    window.kind.set("idea")
    window._send()
    pump(window, lambda: sent and window.sent)
    message = sent[0]
    assert message.kind == "idea" and message.title == "La voz se corta" and message.system == "PC de prueba"
    assert message.images == window.images
    assert "Enviado." in str(window.status.cget("text"))


def test_if_it_cannot_send_it_prepares_the_mail(window, monkeypatch, tmp_path):
    def offline(_message):
        raise OSError("sin internet")

    prepared = []
    monkeypatch.setattr(support, "send", offline)
    monkeypatch.setattr(support, "fallback", lambda message: prepared.append(message) or tmp_path / "Bubble - soporte")
    fill(window)
    window._send()
    pump(window, lambda: prepared)
    window.window.update()
    assert prepared and "sin internet" in str(window.status.cget("text"))
    assert "disabled" not in window.send_button.state()  # permite reintentar el envío
