import json
import os
import stat
from pathlib import Path

import pytest

from nyrun import cli
from nyrun.config import Settings, State, config_dir
from nyrun.player import VLCNotFound, build_command, find_vlc, launch


def test_createfolder_sets_default_and_structure(tmp_path, capsys):
    target = tmp_path / "MyNyRun"
    assert cli.main(["-createfolder", str(target)]) == 0
    assert all((target / c).is_dir() for c in ("Movies", "Series", "Shows"))
    assert Settings.load().default_folder == str(target.resolve())


def test_createfolder_does_not_steal_default(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    cli.main(["-createfolder", str(a)])
    cli.main(["-createfolder", str(b)])
    assert Settings.load().default_folder == str(a.resolve())


def test_createfolder_without_path_uses_home(isolated_env):
    assert cli.main(["-createfolder"]) == 0
    assert (isolated_env / "NyRun" / "Movies").is_dir()


def test_createfolder_is_idempotent(tmp_path):
    cli.main(["-createfolder", str(tmp_path / "N")])
    (tmp_path / "N" / "Movies" / "keep.txt").write_text("x")
    assert cli.main(["-createfolder", str(tmp_path / "N")]) == 0
    assert (tmp_path / "N" / "Movies" / "keep.txt").exists()


def test_default_requires_existing_folder(tmp_path):
    assert cli.main(["-default", str(tmp_path / "missing")]) == 1
    assert Settings.load().default_folder == ""


def test_default_repairs_structure(tmp_path):
    (tmp_path / "Plain").mkdir()
    assert cli.main(["-default", str(tmp_path / "Plain")]) == 0
    assert (tmp_path / "Plain" / "Series").is_dir()


def test_default_rejects_file(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    assert cli.main(["-default", str(f)]) == 1


def test_double_dash_aliases(tmp_path):
    assert cli.main(["--createfolder", str(tmp_path / "Z")]) == 0
    assert cli.main(["--default", str(tmp_path / "Z")]) == 0


def test_launch_without_setup_explains(capsys):
    assert cli.main([]) == 1
    assert "createfolder" in capsys.readouterr().out


def test_theme_validation():
    assert cli.main(["-theme", "nonexistent"]) == 1
    assert cli.main(["-theme", "midnight"]) == 0
    assert Settings.load().theme == "midnight"
    assert cli.main(["-theme", "tokyo-night"]) == 0
    assert cli.main(["-theme", "terminal"]) == 0 and Settings.load().theme == "terminal"


def test_version_and_help(capsys):
    assert cli.main(["-version"]) == 0
    assert cli.main(["-help"]) == 0
    assert "usage" in capsys.readouterr().out


def test_list(library, capsys):
    Settings(default_folder=str(library)).save()
    assert cli.main(["-list"]) == 0
    out = capsys.readouterr().out
    assert "Inception" in out and "Cowboy Bebop" in out


def test_settings_roundtrip_and_unknown_keys():
    s = Settings(default_folder="/x", theme="mono")
    s.save()
    path = config_dir() / "config.json"
    data = json.loads(path.read_text())
    data["future_option"] = 1  # older versions must ignore unknown keys
    path.write_text(json.dumps(data))
    assert Settings.load().theme == "mono"


def test_corrupt_config_falls_back_to_defaults():
    p = config_dir() / "config.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ not json")
    assert Settings.load().default_folder == ""


def test_state_persistence():
    st = State()
    assert st.toggle_watched("Movies/A") is True
    st.toggle_favorite("Movies/A")
    st.mark_played("Series/S/ep1", parent_key="Series/S", episode="Series/S/ep1")
    st2 = State()
    assert "Movies/A" in st2.watched and "Movies/A" in st2.favorites
    assert st2.last_episode["Series/S"] == "Series/S/ep1"
    assert st2.play_count["Series/S"] == 1
    assert st2.toggle_watched("Movies/A") is False


# ------------------------------- player -------------------------------------

def test_build_command_orders_args():
    s = Settings(extra_vlc_args=["--x"])
    cmd = build_command([Path("/a b.mkv"), Path("/c.mkv")], ["vlc"], s)
    assert cmd == ["vlc", "--fullscreen", "--play-and-exit", "--x", "--", "/a b.mkv", "/c.mkv"]
    assert "--fullscreen" not in build_command([Path("/a")], ["vlc"], Settings(fullscreen=False))


def test_find_vlc_custom_and_env(tmp_path, monkeypatch):
    exe = tmp_path / "myvlc"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    assert find_vlc(str(exe)) == [str(exe)]
    monkeypatch.setenv("NYRUN_VLC", str(exe))
    assert find_vlc() == [str(exe)]


def test_find_vlc_missing(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda *_: None)
    monkeypatch.setenv("PATH", "")
    with pytest.raises(VLCNotFound):
        find_vlc()


def test_launch_passes_paths_with_spaces(fake_vlc, tmp_path):
    f = tmp_path / "My Film (2020).mkv"
    f.write_bytes(b"0")
    proc = launch([f], Settings())
    proc.wait(timeout=5)
    assert str(f) in fake_vlc.read_text()
    assert "--fullscreen" in fake_vlc.read_text()


def test_cli_import_moves_into_library(library, isolated_env):
    Settings(default_folder=str(library)).save()
    v = isolated_env / "Downloads" / "Heat.1995.mkv"
    v.write_bytes(b"0")
    assert cli.main(["-import", str(v), "-to", "movies", "-title", "Heat (1995)"]) == 0
    assert (library / "Movies" / "Heat (1995)" / "Heat.1995.mkv").exists() and not v.exists()


def test_cli_import_copy_and_errors(library, isolated_env):
    Settings(default_folder=str(library)).save()
    v = isolated_env / "Downloads" / "x.mkv"
    v.write_bytes(b"0")
    assert cli.main(["-import", str(v), "-to", "Shows", "-copy"]) == 0 and v.exists()
    assert cli.main(["-import", str(v), "-to", "Nope"]) == 1
    assert cli.main(["-import", str(isolated_env / "missing.mkv")]) == 1


def test_install_script_runs_the_right_pipx_commands(tmp_path):
    import os, shutil, stat, subprocess
    from pathlib import Path as P

    script = P(__file__).resolve().parent.parent / "install.sh"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "pipx.log"
    for name, body in {
        "pipx": f'#!/bin/sh\necho "pipx $*" >> {log}\n',
        "nyrun": '#!/bin/sh\necho "nyrun 9.9.9"\n',
    }.items():
        f = bindir / name
        f.write_text(body)
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", HOME=str(tmp_path), XDG_CONFIG_HOME=str(tmp_path / "c"))
    for action in ("install", "update"):
        r = subprocess.run(["bash", str(script), action], env=env, capture_output=True, text=True, input="")
        assert r.returncode == 0, r.stdout + r.stderr
    lines = log.read_text().splitlines()
    installs = [l for l in lines if l.startswith("pipx install ")]
    assert len(installs) == 2 and not any("--force" in l for l in installs)
    assert lines.count("pipx uninstall nyrun") == 2  # old copy removed before each install
    r = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, input="4\n")
    assert r.returncode == 0 and "Install" in r.stdout and "Quit" in r.stdout
    r = subprocess.run(["bash", str(script), "uninstall"], env=env, capture_output=True, text=True, input="n\n")
    assert log.read_text().count("pipx uninstall nyrun") == 3
