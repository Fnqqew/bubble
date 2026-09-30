"""Soporte: un mensaje desde Bubble (título, qué pasó y cómo, imágenes) que le llega por mail al creador.

Se manda con FormSubmit (formsubmit.co), un servicio gratuito de formularios que reenvía cada mensaje por mail, con
las imágenes adjuntas (hasta 10 MB en total). El primer mensaje que recibe una dirección llega como un pedido de
confirmación: el dueño lo confirma una vez y desde ahí llegan todos.

Si no se puede (sin internet, el servicio no responde), el mensaje y las imágenes se guardan en una carpeta y se
abre el mail con el texto listo, para mandarlo a mano.
"""

from __future__ import annotations

import io
import logging
import os
import platform
import urllib.parse
import urllib.request
import uuid
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)
SUPPORT_EMAIL = "juanmartindeza14@gmail.com"
ENDPOINT = f"https://formsubmit.co/{SUPPORT_EMAIL}"
MAX_BYTES = 9_500_000  # FormSubmit acepta hasta 10 MB entre todos los archivos
MAX_IMAGES = 6
KINDS = {"problema": "Problema", "idea": "Idea", "otro": "Otra cosa"}


@dataclass
class Report:
    kind: str
    title: str
    description: str
    contact: str = ""  # tu mail, si querés que te respondan
    images: list[Path] = field(default_factory=list)
    system: str = ""  # datos de tu PC (sin datos personales), si los adjuntás
    log: str = ""  # lo último del registro de errores, si lo adjuntás

    def subject(self) -> str:
        return f"[Bubble · {KINDS.get(self.kind, 'Mensaje')}] {self.title.strip()}"

    def body(self) -> str:
        parts = [self.description.strip()]
        if self.contact.strip():
            parts.append(f"Responder a: {self.contact.strip()}")
        if self.system:
            parts.append("— Su equipo —\n" + self.system)
        if self.log:
            parts.append("— Registro de errores (lo último) —\n" + self.log)
        return "\n\n".join(parts)


def system_text() -> str:
    """Los datos de la PC para el mensaje (versión de Bubble, Windows, procesador…; nada personal)."""
    from . import __version__, pro

    lines = [f"Bubble {__version__} · {'Pro' if pro.active() else 'Basic'}", f"Python {platform.python_version()}"]
    try:
        from .system import detect, summary_lines

        lines += [f"{name}: {value}" for name, value in summary_lines(detect())]
    except Exception:  # noqa: BLE001 - sin los datos igual se manda
        lines.append(f"Windows: {platform.platform()}")
    return "\n".join(lines)


def log_text(lines: int = 120) -> str:
    """Lo último del registro de errores (errores.log)."""
    from .state import state_path

    path = state_path().with_name("errores.log")
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def prepare_image(path: Path, max_side: int = 1920) -> tuple[str, bytes]:
    """La imagen, achicada a lo razonable y en JPG (una captura de pantalla PNG pesa 3–5 MB; así, ~300 KB)."""
    from PIL import Image

    with Image.open(path) as image:
        image = image.convert("RGB")
        if max(image.size) > max_side:
            image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        image.save(out, "JPEG", quality=85, optimize=True)
    return f"{Path(path).stem[:40]}.jpg", out.getvalue()


def _multipart(fields: dict[str, str], files: list[tuple[str, str, bytes]]) -> tuple[bytes, str]:
    boundary = f"----bubble{uuid.uuid4().hex}"
    body = io.BytesIO()
    for name, value in fields.items():
        body.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n".encode())
        body.write(value.encode("utf-8") + b"\r\n")
    for name, filename, data in files:
        body.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
                   f"filename=\"{filename}\"\r\nContent-Type: image/jpeg\r\n\r\n".encode())
        body.write(data + b"\r\n")
    body.write(f"--{boundary}--\r\n".encode())
    return body.getvalue(), f"multipart/form-data; boundary={boundary}"


def send(report: Report, endpoint: str = ENDPOINT, timeout: float = 30.0) -> str:
    """Manda el mensaje. Devuelve un texto para mostrar; si no se pudo, lanza OSError."""
    files: list[tuple[str, str, bytes]] = []
    total = 0
    for index, path in enumerate(report.images[:MAX_IMAGES], start=1):
        filename, data = prepare_image(path)
        if total + len(data) > MAX_BYTES:
            break  # lo que no entra no se manda (el resto del mensaje sí)
        files.append((f"imagen{index}", filename, data))
        total += len(data)
    fields = {"_subject": report.subject(), "_template": "box", "_captcha": "false",
              "Tipo": KINDS.get(report.kind, report.kind), "Título": report.title.strip(), "Mensaje": report.body()}
    if report.contact.strip():
        fields["_replyto"] = report.contact.strip()
        fields["email"] = report.contact.strip()
    body, content_type = _multipart(fields, files)
    request = urllib.request.Request(endpoint, data=body, method="POST",
                                     headers={"Content-Type": content_type, "User-Agent": "Bubble",
                                              "Referer": "https://github.com/Fnqqew/bubble"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        page = response.read(4000).decode("utf-8", "replace").lower()
        if response.status >= 400:
            raise OSError(f"el servicio respondió {response.status}")
    if "activat" in page or "confirm" in page:
        return "Enviado. Como es el primer mensaje, hay que confirmar el formulario desde el mail."
    return "¡Enviado! Gracias: te van a leer."


def fallback(report: Report) -> Path:
    """Si no se pudo mandar: el mensaje y las imágenes en una carpeta (en el escritorio) y el mail listo para mandar
    a mano (con las imágenes adjuntadas desde esa carpeta)."""
    import shutil

    desktop = Path(os.path.expandvars(r"%USERPROFILE%\Desktop"))
    folder = (desktop if desktop.exists() else Path.home()) / "Bubble - soporte"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "mensaje.txt").write_text(f"{report.subject()}\n\n{report.body()}", encoding="utf-8")
    for path in report.images[:MAX_IMAGES]:
        try:
            shutil.copy(path, folder / Path(path).name)
        except OSError:
            pass
    query = urllib.parse.urlencode({"view": "cm", "to": SUPPORT_EMAIL, "su": report.subject(),
                                    "body": report.body()[:1800]}, quote_via=urllib.parse.quote)
    webbrowser.open(f"https://mail.google.com/mail/?{query}")
    os.startfile(folder)  # noqa: S606 - la carpeta, para arrastrar las imágenes al mail
    return folder


def remember(report: Report) -> None:
    """El último mensaje enviado (por si hace falta volver a mandarlo)."""
    from .state import update_state

    update_state(last_support={"subject": report.subject(), "images": len(report.images)})
