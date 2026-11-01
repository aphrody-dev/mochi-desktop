"""``mochi-agent-signal``: tell a running Mochi what a coding agent is doing.

Claude Code and Codex hooks run this command. It forwards one allow-listed
lifecycle word and an opaque per-session token to Mochi's ``agent-event``
application action over D-Bus. The hook payload is read only to hash its
``session_id``; nothing else is kept, logged, or sent.

It never writes output and always exits 0: hook stdout can be added to the
agent's context, and exit code 2 would block the agent.
"""

from __future__ import annotations

import hashlib
import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence

from mochi.agent_activity import AGENT_EVENTS

APPLICATION_ID = "io.github.mochi_desktop.Mochi"
OBJECT_PATH = "/io/github/mochi_desktop/Mochi"
ACTION_NAME = "agent-event"
SOURCES = frozenset({"claude", "codex"})
STDIN_DEADLINE_SECONDS = 0.5
GDBUS_TIMEOUT_SECONDS = 1
PROCESS_TIMEOUT_SECONDS = 1.5
MAX_STDIN_BYTES = 8 * 1024 * 1024


def parse_args(argv: Sequence[str]) -> tuple[str, str] | None:
    """Return ``(event, source)``, or ``None`` for anything unexpected.

    Parsed by hand: a standard argument parser prints usage and exits 2 on a
    mistake, and exit code 2 from a hook blocks the agent.
    """
    args = list(argv)
    if not args:
        return None
    event, rest = args[0], args[1:]
    source = "claude"
    if rest:
        if len(rest) != 2 or rest[0] != "--source":
            return None
        source = rest[1]
    if event not in AGENT_EVENTS or source not in SOURCES:
        return None
    return event, source


def read_payload(stream, *, deadline_seconds: float) -> bytes:
    """Drain the hook payload without ever blocking past the deadline."""
    try:
        if stream is None or stream.isatty():
            return b""
        fd = stream.fileno()
    except (AttributeError, OSError, ValueError):
        return b""
    chunks: list[bytes] = []
    kept = 0

    def keep(chunk: bytes) -> None:
        nonlocal kept
        if kept < MAX_STDIN_BYTES:
            chunks.append(chunk[: MAX_STDIN_BYTES - kept])
            kept += len(chunks[-1])

    deadline = time.monotonic() + deadline_seconds
    if os.name == "nt":
        import ctypes
        import ctypes.wintypes
        import msvcrt

        try:
            handle = msvcrt.get_osfhandle(fd)
        except (OSError, ValueError):
            handle = -1
        peek_pipe = ctypes.windll.kernel32.PeekNamedPipe
        file_type = ctypes.windll.kernel32.GetFileType(handle) if handle != -1 else 0
        # FILE_TYPE_PIPE == 0x0003; regular files (0x0001) and NUL/char (0x0002) never block
        if file_type == 3:
            avail = ctypes.wintypes.DWORD(0)
            while kept < MAX_STDIN_BYTES and time.monotonic() < deadline:
                ok = peek_pipe(
                    handle,
                    None,
                    0,
                    None,
                    ctypes.byref(avail),
                    None,
                )
                if not ok:
                    # Broken pipe / EOF on write end
                    break
                if avail.value > 0:
                    to_read = min(65536, int(avail.value), MAX_STDIN_BYTES - kept)
                    chunk = os.read(fd, to_read)
                    if not chunk:
                        break
                    keep(chunk)
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    time.sleep(min(0.01, remaining))
            return b"".join(chunks)
        try:
            while kept < MAX_STDIN_BYTES and (chunk := os.read(fd, 65536)):
                keep(chunk)
        except (OSError, ValueError):
            pass
        return b"".join(chunks)

    with selectors.DefaultSelector() as selector:
        try:
            selector.register(fd, selectors.EVENT_READ)
            while (remaining := deadline - time.monotonic()) > 0:
                if not selector.select(remaining):
                    break
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                keep(chunk)
        except (OSError, ValueError):
            # epoll refuses regular files and /dev/null. Those never block, so
            # a plain bounded read loop is safe for them.
            while kept < MAX_STDIN_BYTES and (chunk := os.read(fd, 65536)):
                keep(chunk)
            return b"".join(chunks)
    return b"".join(chunks)


def session_token(payload: bytes, source: str) -> str:
    """Hash the payload's ``session_id`` into an opaque 16-hex-digit token."""
    session_id = "default"
    try:
        data = json.loads(payload)
    except (ValueError, RecursionError):
        data = None
    if isinstance(data, dict):
        candidate = data.get("session_id")
        if isinstance(candidate, str) and candidate:
            session_id = candidate
    digest = hashlib.sha256(f"{source}:{session_id}".encode("utf-8", "surrogatepass"))
    return digest.hexdigest()[:16]


def gdbus_command(gdbus: str, event: str, token: str) -> list[str]:
    # Both values are validated (allow-list, hex digest), so the GVariant text
    # cannot be broken out of.
    return [
        gdbus, "call", "--session", "--timeout", str(GDBUS_TIMEOUT_SECONDS),
        "--dest", APPLICATION_ID, "--object-path", OBJECT_PATH,
        "--method", "org.gtk.Actions.Activate", ACTION_NAME,
        f"[<('{event}', '{token}')>]", "{}",
    ]


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin=None,
    run: Callable[..., object] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    stdin_deadline_seconds: float = STDIN_DEADLINE_SECONDS,
) -> int:
    if argv is None:
        # Running as the hook command: a Ctrl+C aimed at the agent must not
        # print a traceback or exit non-zero. The run ends within ~2 s anyway.
        try:
            signal.signal(signal.SIGINT, signal.SIG_IGN)
        except (OSError, ValueError):
            pass
    try:
        parsed = parse_args(sys.argv[1:] if argv is None else argv)
        if parsed is None:
            return 0
        event, source = parsed
        payload = read_payload(
            sys.stdin if stdin is None else stdin,
            deadline_seconds=stdin_deadline_seconds,
        )
        token = session_token(payload, source)
        gdbus = which("gdbus")
        if gdbus is None:
            return 0
        run(
            gdbus_command(gdbus, event, token),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=PROCESS_TIMEOUT_SECONDS,
            check=False,
        )
    except BaseException:
        # Includes KeyboardInterrupt: whatever happens, stay silent and exit 0.
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
