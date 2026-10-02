"""Descarga única de los modelos de voz en %LOCALAPPDATA%\\Bubble\\models."""

from __future__ import annotations

import os
import urllib.request
from pathlib import Path
from typing import Callable

Progress = Callable[[str, float], None]  # (qué se descarga, 0..1)


def models_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    path = Path(base) / "Bubble" / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def download(url: str, target: Path, label: str = "", progress: Progress | None = None) -> Path:
    """Descarga `url` en `target` si aún no existe. Escribe primero en un archivo temporal para que una descarga
    interrumpida no deje un archivo incompleto.
    """
    if target.exists() and target.stat().st_size > 0:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "Bubble"})
    with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while chunk := response.read(1 << 16):
            out.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(label or target.name, done / total)
    partial.replace(target)
    return target


# ---------------------------------------------------------------- Whisper liviano
# Los modelos de Whisper de Systran vienen en float16 y CTranslate2 los pasa a int8 cada vez que los carga. Bubble
# publica esos mismos modelos ya pasados a int8: pesan la mitad y entienden exactamente lo mismo (se comprobó con las
# grabaciones del laboratorio de voz). Las convoluciones quedan en float16, como las deja CTranslate2.
WHISPER_URL = "https://github.com/Fnqqew/bubble/releases/download/modelos-1/"
_TOKENIZER = ("whisper-tokenizer.json", 2203239, "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab")
_VOCABULARY = ("whisper-vocabulary.txt", 459861, "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913")
WHISPER_LIGHT = {  # archivo en la carpeta del modelo → (nombre publicado, bytes, sha256)
    "tiny": {"model.bin": ("whisper-tiny-int8.bin", 39452334,
                           "2a9f8398ff432737b5a812551ff6d0215b5d6b9399d5cb195aab865c18b7367a"),
             "config.json": ("whisper-tiny-config.json", 2249,
                             "a73a28cdfe1c43ccc7202fa333d1f89c202477271407ae9a7f19afa52039cac8"),
             "tokenizer.json": _TOKENIZER, "vocabulary.txt": _VOCABULARY},
    "base": {"model.bin": ("whisper-base-int8.bin", 75104236,
                           "7f8d1e54098026fd0449dabb8fa0effd4db50964fb4cc73b0b87abd87dc1c943"),
             "config.json": ("whisper-base-config.json", 2309,
                             "56a6d8110d311f19c8f0471e562832c7527f146b567275bfca59fcf7c184da9a"),
             "tokenizer.json": _TOKENIZER, "vocabulary.txt": _VOCABULARY},
    "small": {"model.bin": ("whisper-small-int8.bin", 246560124,
                            "4cdc8cb0ac57adadddce464510ea031f5a1593a181cd702fda0270463c274ae0"),
              "config.json": ("whisper-small-config.json", 2370,
                              "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828"),
              "tokenizer.json": _TOKENIZER, "vocabulary.txt": _VOCABULARY},
}


def _light_folder(name: str) -> Path:
    return models_dir() / "whisper" / f"{name}-int8"


def whisper_path(name: str) -> str | None:
    """Carpeta del modelo de Whisper listo para usar: el liviano o, en las PCs que ya lo tenían, el de Systran. None si
    todavía no se descargó.
    """
    light = _light_folder(name)
    if (light / "model.bin").is_file():
        return str(light)
    systran = sorted((models_dir() / "whisper").glob(f"models--Systran--faster-whisper-{name}/snapshots/*/model.bin"))
    return str(systran[0].parent) if systran else None


def download_whisper(name: str, label: str = "", progress: Progress | None = None) -> str:
    """Descarga el modelo liviano (ver WHISPER_LIGHT) y comprueba cada archivo. Devuelve su carpeta."""
    import hashlib
    import shutil

    files = WHISPER_LIGHT[name]
    final = _light_folder(name)
    staging = final.with_name(final.name + ".part")
    for local, (published, size, digest) in files.items():
        target = download(WHISPER_URL + published, staging / local, label, progress if size > 1e6 else None)
        hasher = hashlib.sha256()
        with target.open("rb") as data:
            while block := data.read(1 << 20):
                hasher.update(block)
        if target.stat().st_size != size or hasher.hexdigest() != digest:
            target.unlink(missing_ok=True)
            raise ValueError(f"{published} llegó dañado")
    shutil.rmtree(final, ignore_errors=True)
    staging.replace(final)
    return str(final)
