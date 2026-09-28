"""Instalar lo que falta y desinstalar todo (siempre sobre carpetas de prueba: nunca toca lo tuyo ni el audio)."""

import tkinter as tk

import pytest

from bubble import install, uninstall


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    roaming, local = tmp_path / "roaming", tmp_path / "local"
    monkeypatch.setenv("APPDATA", str(roaming))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    (roaming / "Bubble").mkdir(parents=True)
    (roaming / "Bubble" / "state.json").write_text("{}", encoding="utf-8")
    for folder in ("models/whisper", "descargas", "icons", "tu_voz"):
        (local / "Bubble" / folder).mkdir(parents=True)
    (local / "Bubble" / "models" / "whisper" / "model.bin").write_bytes(b"x" * 2048)
    (local / "Bubble" / "perfil_voz.json").write_text("{}", encoding="utf-8")
    link = tmp_path / "Bubble.lnk"
    link.write_text("acceso", encoding="utf-8")
    monkeypatch.setattr(uninstall, "_desktop_link", lambda: link)
    monkeypatch.setattr(install, "_cable_ready", lambda: False)
    import bubble.voice.devices

    monkeypatch.setattr(bubble.voice.devices, "restore_real_defaults", lambda: [])  # el audio de Windows, ni tocarlo
    later = []
    monkeypatch.setattr(uninstall, "_delete_after_exit", later.extend)
    return tmp_path, roaming / "Bubble", local / "Bubble", link, later


def fake_project(folder, git=False):
    (folder / "src" / "bubble").mkdir(parents=True)
    (folder / "src" / "bubble" / "__init__.py").write_text("", encoding="utf-8")
    (folder / "pyproject.toml").write_text("[project]", encoding="utf-8")
    if git:
        (folder / ".git").mkdir()
    return folder


def test_uninstall_lists_everything_and_protects_development_folders(sandbox):
    tmp, _roaming, _local, _link, _later = sandbox
    dev = {part.key: part for part in uninstall.parts(fake_project(tmp / "dev", git=True))}
    assert set(dev) == {"datos", "modelos", "accesos", "cable", "programa"}
    assert dev["datos"].paths and dev["modelos"].paths and dev["accesos"].paths
    assert "2,0 KB" in dev["modelos"].detail
    assert not dev["programa"].available and not dev["programa"].paths  # carpeta de desarrollo (git): nunca
    assert not dev["cable"].selected  # el micrófono virtual es de Windows: solo si lo elegís
    installed = {part.key: part for part in uninstall.parts(fake_project(tmp / "app"))}
    assert installed["programa"].available and installed["programa"].paths == [tmp / "app"]
    stray = tmp / "otra"
    stray.mkdir()
    assert not {part.key: part for part in uninstall.parts(stray)}["programa"].available  # no es de Bubble


def test_uninstall_deletes_what_you_choose(sandbox):
    tmp, roaming, local, link, later = sandbox
    project = fake_project(tmp / "app")
    done = uninstall.run({"datos", "accesos", "programa"}, project)
    assert not roaming.exists() and not (local / "perfil_voz.json").exists() and not (local / "tu_voz").exists()
    assert (local / "models" / "whisper" / "model.bin").exists()  # los modelos no se eligieron
    assert not link.exists()
    assert later == [project] and project.exists()  # el programa se borra cuando Bubble se cierra
    assert any("carpeta" in line for line in done)
    uninstall.run({"modelos"}, project)
    assert not (local / "models").exists()


def test_missing_only_reports_what_is_needed(monkeypatch):
    fake = [install.Step("a", "A", "", lambda: True, lambda p: ""),
            install.Step("b", "B", "", lambda: False, lambda p: ""),
            install.Step("c", "C", "", lambda: False, lambda p: "", required=False),
            install.Step("d", "D", "", lambda: 1 / 0, lambda p: "")]
    monkeypatch.setattr(install, "steps", lambda: fake)
    assert [s.key for s in install.missing()] == ["b", "d"]  # si no se puede revisar, se da por faltante
    assert [s.key for s in install.missing(only_required=False)] == ["b", "c", "d"]
    assert install.automatic(fake[0]) and not install.automatic(install.Step("e", "", "", bool, bool, action="Sí"))


def test_setup_installs_in_order_and_skips_what_depends_on_a_failure(monkeypatch):
    from bubble.ui import motion
    from bubble.ui.setup_window import SetupWindow

    monkeypatch.setattr(motion, "appear", lambda window, *a, **k: window.attributes("-alpha", 0.0))
    ran, state = [], {"voz": False}

    def run_voice(progress):
        progress("Descargando…", 0.5)
        ran.append("voz")
        state["voz"] = True
        return "listo"

    def broken(progress):
        ran.append("roto")
        raise RuntimeError("sin internet")

    steps = [install.Step("voz", "Voz", "", lambda: state["voz"], run_voice),
             install.Step("roto", "Roto", "", lambda: False, broken),
             install.Step("depende", "Depende", "", lambda: False, lambda p: ran.append("depende") or "",
                          after=["roto"]),
             install.Step("manual", "Manual", "", lambda: False, lambda p: ran.append("manual") or "", action="Instalar")]
    root = tk.Tk()
    root.withdraw()
    try:
        calls = []
        window = SetupWindow(root, calls.append, steps, auto=True)
        import time

        deadline = time.monotonic() + 5
        while (window._busy or calls) and time.monotonic() < deadline:
            while calls:
                calls.pop(0)()
            root.update()
            time.sleep(0.01)
        assert ran == ["voz", "roto"]  # lo que depende de algo que falló no se intenta; lo manual espera tu botón
        icons = {key: str(icon.cget("text")) for key, (icon, _detail) in window.rows.items()}
        assert icons["voz"] == "✓" and icons["roto"] == "!" and icons["depende"] == "!" and icons["manual"] == "○"
        assert "No se pudo" in str(window.status.cget("text")) or "Algo" in str(window.status.cget("text"))
    finally:
        root.destroy()
