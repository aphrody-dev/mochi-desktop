"""``mochi-agent-signal`` forwards one word and a hashed token, silently."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import tomllib
from pathlib import Path

import pytest

from mochi import agent_signal


def _token(source: str, session_id: str) -> str:
    return hashlib.sha256(f"{source}:{session_id}".encode()).hexdigest()[:16]


def _pipe_with(payload: bytes):
    read_fd, write_fd = os.pipe()
    os.write(write_fd, payload)
    os.close(write_fd)
    return os.fdopen(read_fd, "rb")


class RecordingRun:
    def __init__(self, error: BaseException | None = None) -> None:
        self.calls: list[tuple[list[str], dict]] = []
        self.error = error

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if self.error is not None:
            raise self.error


def _found(_name: str) -> str:
    return "/usr/bin/gdbus"


# -- Arguments ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "expected"),
    (
        (["working"], ("working", "claude")),
        (["needs_input", "--source", "codex"], ("needs_input", "codex")),
        (["ended", "--source", "claude"], ("ended", "claude")),
    ),
)
def test_parse_args_accepts_event_and_optional_source(argv, expected) -> None:
    assert agent_signal.parse_args(argv) == expected


@pytest.mark.parametrize(
    "argv",
    (
        [],
        ["bogus"],
        ["WORKING"],
        ["working", "--source"],
        ["working", "--source", "gpt"],
        ["working", "extra"],
        ["working", "--source", "codex", "extra"],
        ["--source", "codex", "working"],
    ),
)
def test_parse_args_rejects_anything_unexpected(argv) -> None:
    assert agent_signal.parse_args(argv) is None


# -- Token --------------------------------------------------------------------


def test_token_hashes_source_and_session_id() -> None:
    payload = b'{"session_id": "abc"}'

    assert agent_signal.session_token(payload, "claude") == _token("claude", "abc")
    assert agent_signal.session_token(payload, "codex") == _token("codex", "abc")


@pytest.mark.parametrize(
    "payload",
    (
        b"",
        b"not json",
        b"[1, 2]",
        b'"just a string"',
        b'{"session_id": 5}',
        b'{"session_id": ""}',
        b'{"other": "abc"}',
        b"[" * 100_000,
    ),
)
def test_token_falls_back_to_default_session(payload) -> None:
    assert agent_signal.session_token(payload, "claude") == _token("claude", "default")


def test_token_survives_lone_surrogates() -> None:
    token = agent_signal.session_token(b'{"session_id": "\\ud800"}', "claude")

    assert len(token) == 16
    assert token != _token("claude", "default")


# -- stdin ----------------------------------------------------------------------


def test_read_payload_drains_a_closed_pipe() -> None:
    assert (
        agent_signal.read_payload(_pipe_with(b'{"session_id": "x"}'), deadline_seconds=0.5)
        == b'{"session_id": "x"}'
    )


def test_read_payload_returns_by_the_deadline_when_stdin_never_closes() -> None:
    read_fd, write_fd = os.pipe()
    try:
        started = time.monotonic()
        payload = agent_signal.read_payload(os.fdopen(read_fd, "rb"), deadline_seconds=0.3)
        elapsed = time.monotonic() - started
    finally:
        os.close(write_fd)

    assert payload == b""
    assert elapsed < 0.5


def test_read_payload_handles_dev_null_and_regular_files(tmp_path: Path) -> None:
    # epoll refuses both; without the fallback the event would be lost.
    with open(os.devnull, "rb") as devnull:
        assert agent_signal.read_payload(devnull, deadline_seconds=0.5) == b""

    file_path = tmp_path / "payload.json"
    file_path.write_bytes(b'{"session_id": "file"}')
    with file_path.open("rb") as stream:
        assert agent_signal.read_payload(stream, deadline_seconds=0.5) == b'{"session_id": "file"}'


def test_read_payload_ignores_missing_or_unusable_stdin() -> None:
    class Closed:
        def isatty(self) -> bool:
            raise ValueError("I/O operation on closed file")

    assert agent_signal.read_payload(None, deadline_seconds=0.5) == b""
    assert agent_signal.read_payload(Closed(), deadline_seconds=0.5) == b""


def test_read_payload_keeps_at_most_the_byte_cap(monkeypatch) -> None:
    monkeypatch.setattr(agent_signal, "MAX_STDIN_BYTES", 10)

    assert agent_signal.read_payload(_pipe_with(b"x" * 100), deadline_seconds=0.5) == b"x" * 10


# -- gdbus --------------------------------------------------------------------


def test_gdbus_command_targets_mochis_app_action() -> None:
    assert agent_signal.gdbus_command("/usr/bin/gdbus", "working", "0123456789abcdef") == [
        "/usr/bin/gdbus",
        "call",
        "--session",
        "--timeout",
        "1",
        "--dest",
        "io.github.mochi_desktop.Mochi",
        "--object-path",
        "/io/github/mochi_desktop/Mochi",
        "--method",
        "org.gtk.Actions.Activate",
        "agent-event",
        "[<('working', '0123456789abcdef')>]",
        "{}",
    ]


# -- main -----------------------------------------------------------------------


def test_main_sends_one_quiet_gdbus_call(capsys) -> None:
    run = RecordingRun()

    result = agent_signal.main(
        ["working"],
        stdin=_pipe_with(b'{"session_id": "abc", "prompt": "secret"}'),
        run=run,
        which=_found,
    )

    assert result == 0
    [(command, kwargs)] = run.calls
    assert command == agent_signal.gdbus_command(
        "/usr/bin/gdbus", "working", _token("claude", "abc")
    )
    assert kwargs == {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "timeout": 1.5,
        "check": False,
    }
    assert capsys.readouterr() == ("", "")


def test_only_session_id_reaches_gdbus() -> None:
    run = RecordingRun()
    first = {
        "session_id": "same",
        "prompt": "fix the login bug",
        "cwd": "/home/me/secret-project",
        "transcript_path": "/home/me/.claude/t.jsonl",
    }
    second = {
        "session_id": "same",
        "prompt_text": "something else entirely",
        "tool_input": {"command": "rm -rf build"},
        "last_assistant_message": "done",
    }

    for payload in (first, second):
        agent_signal.main(
            ["activity"], stdin=_pipe_with(json.dumps(payload).encode()), run=run, which=_found
        )

    assert run.calls[0][0] == run.calls[1][0]
    flattened = " ".join(run.calls[0][0])
    for secret in ("login", "secret-project", "rm -rf", "transcript"):
        assert secret not in flattened


def test_main_ignores_bad_arguments_without_reading_or_calling(capsys) -> None:
    run = RecordingRun()

    class ExplodingStdin:
        def isatty(self) -> bool:
            raise AssertionError("stdin must not be read for bad arguments")

    for argv in ([], ["bogus"], ["working", "--source", "gpt"]):
        assert agent_signal.main(argv, stdin=ExplodingStdin(), run=run, which=_found) == 0

    assert run.calls == []
    assert capsys.readouterr() == ("", "")


def test_main_is_silent_when_gdbus_is_missing(capsys) -> None:
    run = RecordingRun()

    result = agent_signal.main(
        ["working"], stdin=_pipe_with(b"{}"), run=run, which=lambda _name: None
    )

    assert result == 0
    assert run.calls == []
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    "error",
    (
        FileNotFoundError("gdbus"),
        subprocess.TimeoutExpired("gdbus", 1.5),
        OSError("boom"),
        RuntimeError("unexpected"),
    ),
)
def test_main_swallows_gdbus_failures(capsys, error) -> None:
    run = RecordingRun(error=error)

    assert agent_signal.main(["finished"], stdin=_pipe_with(b"{}"), run=run, which=_found) == 0
    assert len(run.calls) == 1
    assert capsys.readouterr() == ("", "")


def test_console_script_is_declared() -> None:
    pyproject = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert pyproject["project"]["scripts"]["mochi-agent-signal"] == "mochi.agent_signal:main"


def test_bridge_never_imports_gi_or_argparse() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "src" / "mochi" / "agent_signal.py"
    ).read_text(encoding="utf-8")

    for forbidden in ("import gi", "from gi", "argparse", "print("):
        assert forbidden not in source


def test_main_swallows_keyboard_interrupt(capsys) -> None:
    run = RecordingRun(error=KeyboardInterrupt())

    assert agent_signal.main(["working"], stdin=_pipe_with(b"{}"), run=run, which=_found) == 0
    assert capsys.readouterr() == ("", "")


def test_ctrl_c_while_waiting_on_stdin_still_exits_quietly() -> None:
    import signal
    import sys

    root = Path(__file__).resolve().parents[1]
    process = subprocess.Popen(
        [sys.executable, "-m", "mochi.agent_signal", "working"],
        stdin=subprocess.PIPE,  # never written or closed: the bridge waits
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": str(root / "src"), "PATH": ""},
    )
    try:
        time.sleep(0.2)
        if os.name == "nt":
            process.terminate()
        else:
            process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()

    if os.name != "nt":
        assert process.returncode == 0
    assert (stdout, stderr) == (b"", b"")
