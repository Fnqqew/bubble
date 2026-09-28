"""Actualizar Bubble: saber si hay una versión nueva, bajarla y ponerla sin tocar lo tuyo (siempre sobre carpetas de
prueba y un servidor local: nunca toca tu copia de Bubble ni pregunta a GitHub de verdad)."""

import http.server
import json
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import zipfile
from pathlib import Path

import pytest

from bubble import update, update_helper


def make_project(folder: Path, version: str, deps: str = '"numpy>=2"', extra: dict | None = None) -> Path:
    (folder / "src" / "bubble").mkdir(parents=True)
    (folder / "src" / "bubble" / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    (folder / "pyproject.toml").write_text(f'[project]\nname = "bubble"\nversion = "{version}"\n'
                                           f'dependencies = [{deps}]\n', encoding="utf-8")
    (folder / "Iniciar.bat").write_text(f"rem {version}\n", encoding="utf-8")
    for name, text in (extra or {}).items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(text, encoding="utf-8")
    return folder


# ---------------------------------------------------------------- versiones
@pytest.mark.parametrize("latest, current, newer", [
    ("3.4.0", "3.3.0", True), ("v3.10.0", "3.9.2", True), ("3.3", "3.3.0", False), ("3.3.0", "3.3.1", False),
    ("4", "3.9.9", True), ("", "3.3.0", False), ("beta", "3.3.0", False),
])
def test_knows_which_version_is_newer(latest, current, newer):
    assert update.is_newer(latest, current) is newer


def test_release_notes_read_as_plain_text():
    notes = update.plain_notes("""Una versión para todos.

<p align="center"><img src="x.gif"></p>

## Se adapta a tu PC

- **Poca memoria:** usa menos. Mirá [la guía](docs/GUIA.md).
> Un aviso importante.

Co-Authored-By: Claude <noreply@anthropic.com>""")
    assert notes == ("Una versión para todos.\n\nSe adapta a tu PC\n\n• Poca memoria: usa menos. Mirá la guía.\n"
                     "Un aviso importante.")
    long = update.plain_notes("\n".join(f"línea {n}" for n in range(200)), limit=60)
    assert long.endswith("\n…") and len(long) < 70


def test_knows_how_bubble_was_installed(tmp_path):
    zipped = make_project(tmp_path / "zip", "3.3.0")
    assert update.install_kind(zipped) == "zip"
    (zipped / ".git").mkdir()
    assert update.install_kind(zipped) == "git"
    assert update.install_kind(tmp_path / "otra") == ""  # no es una carpeta de Bubble: no se toca


# ---------------------------------------------------------------- GitHub (un servidor de prueba)
class Server(http.server.BaseHTTPRequestHandler):
    routes: dict = {}

    def do_GET(self):  # noqa: N802
        status, body = self.routes.get(self.path, (404, b"{}"))
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def github(monkeypatch):
    Server.routes = {}
    httpd = http.server.HTTPServer(("127.0.0.1", 0), Server)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    monkeypatch.setattr(update, "API_LATEST", f"{base}/latest")
    yield base, Server.routes
    httpd.shutdown()


def test_asks_github_for_the_latest_release(github):
    base, routes = github
    routes["/latest"] = (200, json.dumps({"tag_name": "v3.4.0", "body": "## Novedades", "html_url": "https://x/r"})
                         .encode())
    release = update.latest_from_github()
    assert release.version == "3.4.0" and release.notes == "## Novedades" and release.url == "https://x/r"
    assert release.zip_url.endswith("/archive/refs/tags/v3.4.0.zip")


def test_a_private_repository_just_means_no_news(github):
    assert update.latest_from_github() is None  # GitHub responde 404 a quien no tiene cuenta


def test_asks_at_most_twice_a_day_and_respects_later(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    asked = []
    monkeypatch.setattr(update, "latest", lambda: asked.append(1) or update.Release("3.4.0", "notas"))
    release = update.check(current="3.3.0")
    assert release.version == "3.4.0" and len(asked) == 1
    assert update.check(current="3.3.0").version == "3.4.0" and len(asked) == 1  # usa lo que ya sabe
    assert update.check(current="3.4.0") is None  # (ya la tenés)
    update.check(force=True, current="3.3.0")
    assert len(asked) == 2  # «Buscar actualizaciones»: pregunta igual
    assert update.should_offer(release)
    update.remind_later(release)
    assert not update.should_offer(release)  # «Más tarde»: no vuelve a preguntar hoy…
    assert update.should_offer(update.Release("3.5.0"))  # …salvo que salga otra


def test_downloads_and_prepares_a_zip_update(github, monkeypatch, tmp_path):
    base, routes = github
    install = make_project(tmp_path / "Bubble", "3.3.0")
    new = make_project(tmp_path / "armar" / "bubble-3.4.0", "3.4.0")
    archive = tmp_path / "v3.4.0.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for path in new.rglob("*"):
            bundle.write(path, path.relative_to(new.parent))
    routes["/v3.4.0.zip"] = (200, archive.read_bytes())
    monkeypatch.setattr(update, "work_dir", lambda: tmp_path / "trabajo")
    monkeypatch.setenv("APPDATA", str(tmp_path / "datos"))
    steps = []
    work = update.prepare(update.Release("3.4.0", zip_url=f"{base}/v3.4.0.zip"), lambda *a: steps.append(a),
                          folder=install)
    plan = json.loads((work / "plan.json").read_text(encoding="utf-8"))
    assert plan["kind"] == "zip" and plan["project"] == str(install) and plan["version"] == "3.4.0"
    assert (Path(plan["staged"]) / "src" / "bubble" / "__init__.py").read_text().count("3.4.0")
    assert (work / "actualizar.py").read_text(encoding="utf-8") == Path(update_helper.__file__).read_text("utf-8")
    assert any(step[0].startswith("Bajando Bubble 3.4.0…") for step in steps) and steps[-1][1] == 1.0
    routes["/roto.zip"] = (200, b"no es un zip")
    with pytest.raises(update.UpdateError):
        update.prepare(update.Release("3.4.0", zip_url=f"{base}/roto.zip"), lambda *a: None, folder=install)


# ---------------------------------------------------------------- poner la versión nueva
def test_replaces_bubble_but_never_your_environment(tmp_path):
    install = make_project(tmp_path / "Bubble", "3.3.0", extra={"src/bubble/viejo.py": "x", "docs/a.md": "viejo",
                                                                ".venv/pyvenv.cfg": "mío", "mis-notas.txt": "mío"})
    staged = make_project(tmp_path / "nueva", "3.4.0", extra={"docs/a.md": "nuevo", "docs/b.md": "nuevo"})
    assert update_helper.replace_files(install, staged) is False  # misma lista de paquetes: no hace falta pip
    assert '"3.4.0"' in (install / "src" / "bubble" / "__init__.py").read_text()
    assert not (install / "src" / "bubble" / "viejo.py").exists()  # lo que ya no existe se va
    assert (install / "docs" / "b.md").read_text() == "nuevo" and (install / "docs" / "a.md").read_text() == "nuevo"
    assert (install / ".venv" / "pyvenv.cfg").read_text(encoding="utf-8") == "mío"
    assert (install / "mis-notas.txt").read_text(encoding="utf-8") == "mío"
    assert not (install / "src" / "bubble.anterior").exists()
    newer = make_project(tmp_path / "otra", "3.5.0", deps='"numpy>=2", "httpx"')
    assert update_helper.replace_files(install, newer) is True  # paquetes nuevos: se reinstala


def test_if_copying_fails_the_old_version_stays(tmp_path, monkeypatch):
    install = make_project(tmp_path / "Bubble", "3.3.0", extra={"src/bubble/viejo.py": "x"})
    staged = make_project(tmp_path / "nueva", "3.4.0")

    def broken(source, target, **kwargs):
        Path(target).mkdir(parents=True)
        raise OSError("disco lleno")

    monkeypatch.setattr(update_helper.shutil, "copytree", broken)
    with pytest.raises(OSError):
        update_helper.replace_files(install, staged)
    assert '"3.3.0"' in (install / "src" / "bubble" / "__init__.py").read_text()
    assert (install / "src" / "bubble" / "viejo.py").exists() and not (install / "src" / "bubble.anterior").exists()


def run_helper(tmp_path, plan: dict) -> dict:
    """El que termina la actualización, como proceso aparte (como de verdad), sin ventana."""
    done = subprocess.run([sys.executable, "-c", "pass"])  # un «Bubble» que ya se cerró
    marker = tmp_path / "reabierto.txt"
    plan.update(pid=4_000_000 + done.returncode, python=sys.executable, result=str(tmp_path / "resultado.json"),
                relaunch=[sys.executable, "-c", f"open(r'{marker}', 'w').write('ok')"])
    (tmp_path / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    shutil.copy(update_helper.__file__, tmp_path / "actualizar.py")
    subprocess.run([sys.executable, str(tmp_path / "actualizar.py"), str(tmp_path / "plan.json"), "--sin-ventana"],
                   check=True, timeout=60, capture_output=True)
    for _ in range(100):
        if marker.exists():
            break
        time.sleep(0.05)
    assert marker.exists()  # Bubble se vuelve a abrir
    return json.loads((tmp_path / "resultado.json").read_text(encoding="utf-8"))


def test_the_helper_updates_and_reopens_bubble(tmp_path):
    install = make_project(tmp_path / "Bubble", "3.3.0")
    staged = make_project(tmp_path / "nueva", "3.4.0")
    result = run_helper(tmp_path, {"kind": "zip", "project": str(install), "staged": str(staged), "version": "3.4.0"})
    assert result == {"version": "3.4.0", "ok": True, "error": ""}
    assert '"3.4.0"' in (install / "src" / "bubble" / "__init__.py").read_text()


def test_a_failed_update_still_reopens_bubble_and_says_why(tmp_path):
    install = make_project(tmp_path / "Bubble", "3.3.0")
    result = run_helper(tmp_path, {"kind": "zip", "project": str(install), "staged": str(tmp_path / "no-existe"),
                                   "version": "3.4.0"})
    assert not result["ok"] and result["error"]
    assert '"3.3.0"' in (install / "src" / "bubble" / "__init__.py").read_text()  # quedó la que tenías


def test_finished_is_read_once(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    (tmp_path / "Bubble").mkdir()
    (tmp_path / "Bubble" / "actualizacion.json").write_text('{"version": "3.4.0", "ok": true}', encoding="utf-8")
    assert update.finished() == {"version": "3.4.0", "ok": True}
    assert update.finished() is None


# ---------------------------------------------------------------- con git (una copia de desarrollo)
def git(folder, *args):
    subprocess.run(["git", "-C", str(folder), "-c", "user.name=Prueba", "-c", "user.email=prueba@example.com", *args],
                   check=True, capture_output=True)


@pytest.mark.skipif(shutil.which("git") is None, reason="sin git")
def test_a_git_copy_updates_with_git_pull(tmp_path):
    origin = tmp_path / "origen.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)
    author = make_project(tmp_path / "autor", "3.3.0")
    git(author, "init", "-b", "main")
    git(author, "add", "-A")
    git(author, "commit", "-m", "Bubble 3.3")
    git(author, "tag", "v3.3.0")
    git(author, "remote", "add", "origin", str(origin))
    git(author, "push", "origin", "main", "--tags")
    install = tmp_path / "Bubble"
    subprocess.run(["git", "clone", str(origin), str(install)], check=True, capture_output=True)
    assert update.latest_from_git(install).version == "3.3.0"

    (author / "src" / "bubble" / "__init__.py").write_text('__version__ = "3.4.0"\n', encoding="utf-8")
    git(author, "commit", "-am", "Bubble 3.4: se actualiza solo\n\nAhora te avisa cuando hay una versión nueva.")
    git(author, "tag", "v3.4.0")
    git(author, "push", "origin", "main", "--tags")
    release = update.latest_from_git(install)
    assert release.version == "3.4.0" and "te avisa" in release.notes  # las novedades: el mensaje de esa versión

    (install / "Iniciar.bat").write_text("cambio mío", encoding="utf-8")
    with pytest.raises(update.UpdateError, match="cambios propios"):
        update.prepare(release, lambda *a: None, folder=install)  # nunca pisa lo que cambiaste
    git(install, "checkout", "--", "Iniciar.bat")
    assert update_helper.git_pull(install) is False
    assert '"3.4.0"' in (install / "src" / "bubble" / "__init__.py").read_text()


# ---------------------------------------------------------------- la ventana
@pytest.fixture
def window(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from bubble.ui import widgets
    from bubble.ui.update_window import UpdateWindow

    monkeypatch.setattr(widgets, "present", lambda window, root: None)  # escondida: nunca te saca el foco
    monkeypatch.setattr(update, "install_kind", lambda folder=None: "zip")
    root = tk.Tk()
    root.withdraw()
    pending.clear()
    restarted = []
    try:
        yield UpdateWindow(root, update.Release("3.4.0", "## Novedades\n- **Algo lindo**"), pending.append,
                           lambda: restarted.append(1)), restarted
    finally:
        root.destroy()


pending: list = []  # lo que la ventana pide hacer en su hilo (en Bubble lo hace la cola de eventos)


def pump(window, until, seconds=4.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end and not until():
        while pending:
            pending.pop(0)()
        window.window.update()
        time.sleep(0.02)


def test_later_does_not_ask_again_today(window):
    dialog, restarted = window
    dialog.later.invoke()
    assert not update.should_offer(dialog.release) and not restarted


def test_update_now_prepares_closes_and_lets_the_helper_finish(window, monkeypatch, tmp_path):
    dialog, restarted = window
    launched = []
    monkeypatch.setattr(update, "prepare", lambda release, progress: progress("Bajando…", 0.5) or tmp_path)
    monkeypatch.setattr(update, "launch", launched.append)
    dialog.go.invoke()
    assert "disabled" in dialog.later.state()  # ya no se cancela a la mitad
    pump(dialog, lambda: restarted)
    assert launched == [tmp_path] and restarted == [1]


def test_if_it_cannot_update_it_says_why_and_lets_you_retry(window, monkeypatch):
    dialog, restarted = window

    def refuse(release, progress):
        raise update.UpdateError("Tenés cambios propios sin guardar")

    monkeypatch.setattr(update, "prepare", refuse)
    dialog.go.invoke()
    pump(dialog, lambda: "cambios" in str(dialog.status.cget("text")))
    assert "cambios propios" in str(dialog.status.cget("text")) and not restarted
    assert "disabled" not in dialog.go.state()


def test_notes_cut_every_line_are_joined_back():
    message = ("Bubble 3.4: se actualiza solo\n\nAhora te avisa cuando hay una versión nueva y se\nactualiza con un "
               "clic.\n\n- Más tarde: no vuelve a preguntar\n  hasta mañana.\n- Nunca en medio de una partida.")
    assert update.plain_notes(message) == ("Bubble 3.4: se actualiza solo\n\nAhora te avisa cuando hay una versión "
                                           "nueva y se actualiza con un clic.\n\n• Más tarde: no vuelve a preguntar "
                                           "hasta mañana.\n• Nunca en medio de una partida.")
