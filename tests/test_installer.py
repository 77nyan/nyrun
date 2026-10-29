"""The installer: `sudo ./install.sh` is the only way in, and it installs, updates and removes NyRun."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "install.sh"
IS_ROOT = os.geteuid() == 0
HAVE_SUDO = IS_ROOT or subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0
needs_root = pytest.mark.skipif(not HAVE_SUDO, reason="needs root or passwordless sudo")


def run(args, env_extra=None, input_text="", cwd=ROOT, as_root=True, timeout=600):
    """Run ./install.sh the way a user would (through sudo when we are not root)."""
    env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp"), "TERM": "dumb", "NYRUN_PLAIN": "1", "NO_COLOR": "1"}
    env.update(env_extra or {})
    if as_root and not IS_ROOT:
        cmd = ["sudo", "-n", "env"] + [f"{k}={v}" for k, v in env.items()] + ["./install.sh"] + list(args)
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, input=input_text, timeout=timeout)
    return subprocess.run([str(SCRIPT)] + list(args), cwd=cwd, env=env, capture_output=True, text=True, input=input_text, timeout=timeout)


# ------------------------------------------------------------------ it refuses every other way

def test_script_is_executable():
    assert os.access(SCRIPT, os.X_OK)


@pytest.mark.parametrize("shell", ["sh", "bash"])
def test_running_with_a_shell_is_refused(shell, tmp_path):
    r = subprocess.run([shell, "install.sh"], cwd=ROOT, capture_output=True, text=True, input="", env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)})
    assert r.returncode == 1
    assert "sudo ./install.sh" in r.stderr
    assert "Install" not in r.stdout  # no menu, nothing happened


def test_running_without_sudo_is_refused():
    if IS_ROOT:
        pytest.skip("already root")
    r = subprocess.run([str(SCRIPT)], cwd=ROOT, capture_output=True, text=True, input="1\n")
    assert r.returncode == 1 and "sudo ./install.sh" in r.stderr


@needs_root
def test_root_but_through_a_shell_is_refused():
    cmd = ["sh", "install.sh"] if IS_ROOT else ["sudo", "-n", "sh", "install.sh"]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, input="")
    assert r.returncode == 1 and "sudo ./install.sh" in r.stderr


# ------------------------------------------------------------------ the menu

@needs_root
def test_menu_lists_exactly_four_options_and_quits():
    r = run([], input_text="4\n")
    assert r.returncode == 0, r.stdout + r.stderr
    for line in ("1) Install", "2) Update", "3) Uninstall", "4) Quit"):
        assert line in r.stdout


@needs_root
def test_help_and_unknown_option():
    r = run(["--help"])
    assert r.returncode == 0 and "install" in r.stdout and "uninstall" in r.stdout
    r = run(["--frobnicate"])
    assert r.returncode == 2


@needs_root
def test_update_and_uninstall_when_nothing_is_installed(tmp_path):
    env = {"NYRUN_PREFIX": str(tmp_path / "opt"), "NYRUN_BINDIR": str(tmp_path / "bin"), "NYRUN_USER_HOME": str(tmp_path / "home")}
    r = run(["update", "--yes"], env)
    assert r.returncode == 0 and "not installed" in r.stdout
    r = run(["uninstall", "--yes"], env)
    assert r.returncode == 0 and "not installed" in r.stdout


# ------------------------------------------------------------------ the real thing (needs the network for pip)

@needs_root
@pytest.mark.skipif(shutil.which("pip") is None and shutil.which("pip3") is None, reason="no pip")
def test_install_update_uninstall_lifecycle(tmp_path):
    prefix, bindir, home = tmp_path / "opt" / "nyrun", tmp_path / "bin", tmp_path / "home"
    (home / ".config" / "nyrun").mkdir(parents=True)
    (home / ".config" / "nyrun" / "config.json").write_text("{}")
    (home / ".local/share/pipx/venvs/nyrun").mkdir(parents=True)  # an old pipx install to clean up
    env = {"NYRUN_PREFIX": str(prefix), "NYRUN_BINDIR": str(bindir), "NYRUN_USER_HOME": str(home)}

    r = run(["install", "--yes"], env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Installed" in r.stdout
    cmd = bindir / "nyrun"
    assert cmd.is_symlink() and os.readlink(cmd) == str(prefix / "bin" / "nyrun")
    out = subprocess.run([str(cmd), "-version"], capture_output=True, text=True)
    version = (ROOT / "pyproject.toml").read_text().split('version = "')[1].split('"')[0]
    assert out.returncode == 0 and version in out.stdout
    assert not (home / ".local/share/pipx/venvs/nyrun").exists()  # the old pipx copy is gone
    # no build junk left behind in the source folder
    assert not (ROOT / "build").exists() and not list(ROOT.glob("*.egg-info")) and not list((ROOT / "src").glob("*.egg-info"))

    r = run(["update", "--yes"], env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "up to date" in r.stdout or "Updated" in r.stdout
    assert subprocess.run([str(cmd), "-version"], capture_output=True).returncode == 0

    # --yes must never delete settings
    r = run(["uninstall", "--yes"], env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not prefix.exists() and not cmd.exists()
    assert (home / ".config" / "nyrun" / "config.json").exists()


@needs_root
def test_uninstall_leaves_a_foreign_nyrun_command_alone(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "nyrun").write_text("#!/bin/sh\necho mine\n")
    env = {"NYRUN_PREFIX": str(tmp_path / "opt"), "NYRUN_BINDIR": str(bindir), "NYRUN_USER_HOME": str(tmp_path / "home")}
    run(["uninstall", "--yes"], env)
    assert (bindir / "nyrun").read_text().strip().endswith("echo mine")
