"""
Arrow-key menus (scripts/lib/menu.sh and the scripts that use it) driven in a
real pseudo-terminal, so key handling, `set -e` interaction and terminal
restore are tested the way a user hits them.

Runs under every bash it can find (macOS ships 3.2, Linux 5.x; the original
bug — the menu dying on the first ↓ — only showed on 5.x):

    uv run --no-project --with pytest pytest tests/scripts/test_menus.py
    # bash 5 on macOS:
    docker run --rm -v "$PWD":/r -w /r python:3.12-slim \
        sh -c "pip -q install pytest && pytest -q tests/scripts/test_menus.py"
"""

from __future__ import annotations

import os
import pty
import select
import shutil
import signal
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MENU_LIB = REPO / "scripts" / "lib" / "menu.sh"

UP, DOWN, ENTER, ESC, SPACE = b"\x1b[A", b"\x1b[B", b"\r", b"\x1b", b" "

BASHES = sorted({p for p in ("/bin/bash", shutil.which("bash")) if p and os.path.exists(p)})


def _bash_id(path: str) -> str:
    return path


@pytest.fixture(params=BASHES, ids=_bash_id)
def bash(request) -> str:
    return request.param


@pytest.fixture
def fakebin(tmp_path: Path) -> Path:
    """`docker` / `tofu` stand-ins: the menus must never touch a real stack."""
    d = tmp_path / "bin"
    d.mkdir()
    for name in ("docker", "tofu"):
        f = d / name
        f.write_text("#!/bin/sh\nexit 0\n")
        f.chmod(0o755)
    return d


class Session:
    """A process attached to a pty; collects output, sends keys."""

    def __init__(self, argv: list[str], env: dict[str, str], cwd: Path):
        self.pid, self.fd = pty.fork()
        if self.pid == 0:  # child
            os.chdir(cwd)
            os.execvpe(argv[0], argv, env)
        self.output = b""
        self.status: int | None = None
        self.pump(1.5)

    def pump(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            readable, _, _ = select.select([self.fd], [], [], 0.05)
            if readable:
                try:
                    chunk = os.read(self.fd, 65536)
                except OSError:
                    chunk = b""
                if not chunk:  # pty closed: the child is exiting — collect its status
                    self._reap(end)
                    return
                self.output += chunk
            if self.exited():
                # drain what is left
                try:
                    while select.select([self.fd], [], [], 0.05)[0]:
                        chunk = os.read(self.fd, 65536)
                        if not chunk:
                            break
                        self.output += chunk
                except OSError:
                    pass
                return

    def _reap(self, deadline: float) -> None:
        while not self.exited() and time.time() < deadline + 2:
            time.sleep(0.02)

    def exited(self) -> bool:
        if self.status is None:
            pid, raw = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                self.status = os.waitstatus_to_exitcode(raw)
        return self.status is not None

    def send(self, *keys: bytes, settle: float = 0.4) -> None:
        for key in keys:
            if self.exited():
                return
            try:
                os.write(self.fd, key)
            except OSError:
                return
            # A bare ESC is only recognised after the 1 s escape-sequence timeout.
            self.pump(1.4 if key == ESC else settle)

    def text(self) -> str:
        return self.output.decode("utf-8", "replace")

    def close(self) -> None:
        if not self.exited():
            os.kill(self.pid, signal.SIGKILL)
            os.waitpid(self.pid, 0)


def _env(fakebin: Path) -> dict[str, str]:
    return dict(os.environ, PATH=f"{fakebin}:{os.environ['PATH']}", TERM="xterm", LINES="60", COLUMNS="120")


# ── the library ───────────────────────────────────────────────────────────────

HARNESS = r"""
set -e
source "{lib}"
KEYS=(alpha beta gamma)
LBLS=("Alpha" "Beta" "Gamma")
if [ "$1" = multi ]; then
    if menu_multiselect picked "Pick" KEYS LBLS; then echo "RESULT=[${{picked[*]}}]"; else echo "RESULT=CANCELLED"; fi
else
    if menu_select picked "Pick" KEYS LBLS "${{2:-0}}" $3; then echo "RESULT=$picked INDEX=$MENU_INDEX"; else echo "RESULT=CANCELLED"; fi
fi
stty -a | tr ' ' '\n' | grep -qx icanon && echo "TTY=restored" || echo "TTY=raw"
"""


@pytest.fixture
def harness(tmp_path: Path) -> Path:
    script = tmp_path / "harness.sh"
    script.write_text(HARNESS.format(lib=MENU_LIB))
    return script


def run_harness(bash, harness, fakebin, keys, *args):
    s = Session([bash, str(harness), *args], _env(fakebin), REPO)
    try:
        s.send(*keys)
        s.pump(1.0)
        return s.status, s.text()
    finally:
        s.close()


@pytest.mark.parametrize(
    "keys, expected",
    [
        ([DOWN, DOWN, ENTER], "RESULT=gamma INDEX=2"),
        ([UP, UP, ENTER], "RESULT=alpha INDEX=0"),  # ↑ at the top: stays, no crash
        ([DOWN, DOWN, DOWN, DOWN, ENTER], "RESULT=gamma INDEX=2"),  # ↓ past the end
        ([b"j", b"j", b"k", ENTER], "RESULT=beta INDEX=1"),
        ([DOWN, b"x", ENTER], "RESULT=beta INDEX=1"),  # unknown keys ignored
    ],
)
def test_select_moves_and_chooses(bash, harness, fakebin, keys, expected):
    status, out = run_harness(bash, harness, fakebin, keys, "single", "0", "--cancel")
    assert status == 0, out
    assert expected in out
    assert "TTY=restored" in out


def test_select_starts_at_the_preselected_item(bash, harness, fakebin):
    status, out = run_harness(bash, harness, fakebin, [ENTER], "single", "2", "--cancel")
    assert "RESULT=gamma INDEX=2" in out


@pytest.mark.parametrize("key", [b"q", ESC], ids=["q", "esc"])
def test_select_cancels_with_q_or_esc_when_allowed(bash, harness, fakebin, key):
    status, out = run_harness(bash, harness, fakebin, [DOWN, key], "single", "0", "--cancel")
    assert status == 0, out
    assert "RESULT=CANCELLED" in out
    assert "TTY=restored" in out


def test_select_ignores_q_and_esc_without_cancel(bash, harness, fakebin):
    status, out = run_harness(bash, harness, fakebin, [b"q", ESC, DOWN, ENTER], "single", "0")
    assert "RESULT=beta INDEX=1" in out


def test_multiselect_toggles_items(bash, harness, fakebin):
    status, out = run_harness(bash, harness, fakebin, [SPACE, DOWN, DOWN, SPACE, ENTER], "multi")
    assert status == 0, out
    assert "RESULT=[alpha gamma]" in out
    assert "TTY=restored" in out


def test_multiselect_a_selects_all_then_none(bash, harness, fakebin):
    _, all_out = run_harness(bash, harness, fakebin, [b"a", ENTER], "multi")
    _, none_out = run_harness(bash, harness, fakebin, [b"a", b"a", ENTER], "multi")
    assert "RESULT=[alpha beta gamma]" in all_out
    assert "RESULT=[]" in none_out


def test_multiselect_cancels_with_esc(bash, harness, fakebin):
    _, out = run_harness(bash, harness, fakebin, [SPACE, ESC], "multi")
    assert "RESULT=CANCELLED" in out


def test_ctrl_c_restores_the_cursor_and_exits_130(bash, harness, fakebin):
    status, out = run_harness(bash, harness, fakebin, [DOWN, b"\x03"], "single", "0", "--cancel")
    assert status == 130
    assert "\x1b[?25h" in out


def test_menu_never_uses_absolute_cursor_rows(bash, harness, fakebin):
    """Absolute positioning (ESC[<row>;<col>H) overwrote text printed above the menu."""
    _, out = run_harness(bash, harness, fakebin, [DOWN, ENTER], "single", "0", "--cancel")
    assert ";0H" not in out and ";1H" not in out


# ── the real menu scripts ─────────────────────────────────────────────────────


@pytest.mark.parametrize("script", ["docintel.sh", "logs.sh", "test.sh"])
def test_menu_script_survives_navigation_and_esc_exits_cleanly(bash, fakebin, script):
    s = Session([bash, str(REPO / "scripts" / script)], _env(fakebin), REPO)
    try:
        s.send(UP, DOWN, DOWN, DOWN, UP, UP, UP, UP)
        assert not s.exited(), f"{script} died during navigation:\n{s.text()[-800:]}"
        s.send(ESC)
        s.pump(1.5)
        assert s.exited() and s.status == 0, f"{script} did not exit cleanly on Esc:\n{s.text()[-800:]}"
    finally:
        s.close()


def test_docintel_menu_returns_after_an_in_process_action(bash, fakebin):
    """Status runs in-process; the CLI must come back to the menu, not drop to the shell."""
    s = Session([bash, str(REPO / "scripts" / "docintel.sh")], _env(fakebin), REPO)
    try:
        s.send(*([DOWN] * 5))  # Status is the 6th entry
        s.send(ENTER, settle=1.5)
        assert "Container Status" in s.text()
        s.send(b"x", settle=1.5)  # "press any key"
        assert not s.exited(), s.text()[-800:]
        assert s.text().count("DocIntel CLI") >= 2
        s.send(b"q", settle=1.0)
        assert s.exited() and s.status == 0
    finally:
        s.close()


def test_docintel_sub_picker_can_be_cancelled_back_to_the_menu(bash, fakebin):
    """Setup's engine picker used to start setup on the only key it accepted (Enter)."""
    s = Session([bash, str(REPO / "scripts" / "docintel.sh")], _env(fakebin), REPO)
    try:
        s.send(ENTER, settle=1.0)  # Setup → engine picker
        assert "LLM Engine" in s.text()
        s.send(b"q", settle=1.5)  # back out of the picker
        s.send(b"x", settle=1.5)  # "press any key"
        assert not s.exited(), s.text()[-800:]
        assert s.text().count("DocIntel CLI") >= 2
    finally:
        s.close()


# ── logs.sh clear: recreate with the full compose chain, only what runs ────────


def test_logs_clear_recreates_only_running_app_services_with_the_full_compose_chain(tmp_path):
    import subprocess

    calls = tmp_path / "docker-calls"
    fake = tmp_path / "bin"
    fake.mkdir()
    # `docker compose … ps -q <svc>` prints an id only for rag-service and web-ui.
    (fake / "docker").write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> "{calls}"\n'
        'case "$*" in *" ps -q rag-service"|*" ps -q web-ui") echo cid123 ;; esac\n'
        "exit 0\n"
    )
    (fake / "docker").chmod(0o755)
    data_dir = tmp_path / "data"
    env = dict(os.environ, PATH=f"{fake}:{os.environ['PATH']}", DOCINTEL_DATA_DIR=str(data_dir))

    result = subprocess.run(
        ["bash", str(REPO / "scripts" / "logs.sh"), "clear"],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=60,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    up = [line for line in calls.read_text().splitlines() if " up " in f" {line} "]
    assert len(up) == 1, calls.read_text()
    cmd = up[0]
    assert "docker-compose.storage.yml" in cmd  # DOCINTEL_DATA_DIR overlay kept
    assert "docker-compose.override.yml" in cmd
    assert "--force-recreate --no-deps" in cmd
    assert cmd.endswith("rag-service web-ui")  # only the running app services
