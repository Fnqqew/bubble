"""Bubble más liviano: sin scipy ni PyAV, Whisper en int8, pip sin caché y el ZIP de cada versión sin lo que no se usa."""

import hashlib
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from bubble import install, update_helper
from bubble.voice import models

ROOT = Path(__file__).resolve().parent.parent


def test_players_do_not_install_scipy_pyav_or_whisper_with_its_dependencies():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    installed = [*project["dependencies"], *project["optional-dependencies"]["voz"]]
    names = {re.split(r"[<>=!; \[]", requirement)[0].lower() for requirement in installed}
    assert not names & {"scipy", "av", "faster-whisper"}
    assert {"ctranslate2", "huggingface-hub", "tokenizers", "onnxruntime", "tqdm"} <= names  # (lo que sí usa Whisper)


def test_whisper_is_installed_without_dependencies_everywhere_with_the_same_version():
    launcher = (ROOT / "Iniciar.bat").read_text(encoding="ascii")
    assert f"--no-deps {install.WHISPER_PACKAGE}" in launcher
    assert update_helper.WHISPER_PACKAGE == install.WHISPER_PACKAGE
    assert launcher.count("--no-cache-dir") == 3  # pip no guarda copia de lo descargado


def test_whisper_loads_without_pyav():
    blocker = ("import sys, importlib.abc\n"
               "class NoAv(importlib.abc.MetaPathFinder):\n"
               "    def find_spec(self, name, path=None, target=None):\n"
               "        if name == 'av' or name.startswith('av.'): raise ImportError('sin PyAV')\n"
               "sys.meta_path.insert(0, NoAv())\n"
               "import bubble.voice\n"
               "from faster_whisper import WhisperModel\n"
               "from faster_whisper.utils import get_assets_path\n"
               "print('ok')\n")
    done = subprocess.run([sys.executable, "-c", blocker], capture_output=True, text=True, timeout=120)
    assert done.stdout.strip() == "ok", done.stderr[-500:]


def test_setup_installs_voice_packages_without_cache_and_whisper_apart(monkeypatch):
    commands = []

    class Process:
        stdout = iter(["Collecting ctranslate2\n"])

        def __init__(self, command, **_kw):
            commands.append(command)

        def wait(self):
            return 0

    monkeypatch.setattr(install.subprocess, "Popen", Process)
    install._pip_install(lambda *_: None)
    assert len(commands) == 2 and all("--no-cache-dir" in command for command in commands)
    assert commands[1][-2:] == ["--no-deps", install.WHISPER_PACKAGE]


def test_the_updater_installs_the_same_way(monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr(update_helper.subprocess, "run",
                        lambda command, **_kw: commands.append(command) or subprocess.CompletedProcess(command, 0))
    update_helper.pip_install("python", tmp_path)
    assert all("--no-cache-dir" in command for command in commands)
    assert commands[-1][-2:] == ["--no-deps", update_helper.WHISPER_PACKAGE]


# ---------------------------------------------------------------- Whisper liviano
@pytest.fixture
def light(monkeypatch, tmp_path):
    """Un «modelo» chico publicado en una dirección de mentira."""
    monkeypatch.setattr(models, "models_dir", lambda: tmp_path)
    content = {"model.bin": b"pesos en int8", "config.json": b"{}"}
    monkeypatch.setitem(models.WHISPER_LIGHT, "tiny", {
        local: (f"whisper-tiny-{local}", len(data), hashlib.sha256(data).hexdigest()) for local, data in content.items()})
    served = {f"whisper-tiny-{local}": data for local, data in content.items()}

    def download(url, target, label="", progress=None):
        published = url.rsplit("/", 1)[-1]
        if published not in served:
            raise OSError("HTTP Error 404")  # (urllib avisa así: URLError y HTTPError son OSError)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(served[published])
        return target

    monkeypatch.setattr(models, "download", download)
    return tmp_path, served


def test_the_light_model_is_downloaded_checked_and_preferred(light):
    folder, _served = light
    assert models.whisper_path("tiny") is None
    systran = folder / "whisper" / "models--Systran--faster-whisper-tiny" / "snapshots" / "abc"
    systran.mkdir(parents=True)
    (systran / "model.bin").write_bytes(b"float16")
    assert models.whisper_path("tiny") == str(systran)  # quien ya lo tenía no vuelve a descargar nada
    path = models.download_whisper("tiny")
    assert models.whisper_path("tiny") == path and (Path(path) / "model.bin").read_bytes() == b"pesos en int8"
    assert not (folder / "whisper" / "tiny-int8.part").exists()


def test_a_damaged_download_is_not_used(light):
    folder, served = light
    served["whisper-tiny-model.bin"] = b"pesos cortados"
    with pytest.raises(ValueError):
        models.download_whisper("tiny")
    assert models.whisper_path("tiny") is None


def test_if_github_fails_whisper_comes_from_systran(light, monkeypatch):
    import faster_whisper

    _folder, served = light
    served.clear()  # GitHub no responde
    monkeypatch.setattr(install, "_whisper_names", lambda: ["tiny"])
    fetched = []
    monkeypatch.setattr(faster_whisper, "download_model", lambda name, cache_dir: fetched.append(name))
    install._download_whisper(lambda *_: None)
    assert fetched == ["tiny"]


# ---------------------------------------------------------------- limpieza y ZIP
def test_leftovers_are_removed_on_players_pcs_only(monkeypatch):
    present = {"scipy", "av"}
    monkeypatch.setattr(install, "_installed", lambda name: name in present)
    commands = []
    monkeypatch.setattr(install.subprocess, "run",
                        lambda command, **_kw: commands.append(command) or subprocess.CompletedProcess(command, 0))
    assert install.tidy() == ["scipy", "av"] and commands[0][-2:] == ["scipy", "av"]
    present.add("pytest")  # la PC de desarrollo los usa
    assert install.tidy() == [] and len(commands) == 1


def test_the_release_zip_keeps_what_bubble_needs_with_windows_line_endings():
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "*.bat -text" in attributes and "tests/** export-ignore" in attributes
    assert "src/" not in attributes and "locales" not in attributes
    for launcher in ("Iniciar.bat", "Desinstalar.bat"):
        data = (ROOT / launcher).read_bytes()
        assert data.count(b"\n") == data.count(b"\r\n")  # con LF, cmd a veces no encuentra las etiquetas de goto
