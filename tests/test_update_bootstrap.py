"""Updater bootstrap independence tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from mochi.update.bootstrap import _runner_source, bootstrap_updater
from mochi.update.model import UpdateMetadata, UpdateTarget


class _Process:
    pass


def _target() -> UpdateTarget:
    return UpdateTarget(
        commit="abc123",
        metadata=UpdateMetadata(
            version="0.4.0a1",
            channel="main",
            highlights=("one", "two"),
        ),
    )


def test_bootstrap_copies_update_package_and_selected_art_before_launch(
    tmp_path: Path,
) -> None:
    source_package = tmp_path / "source" / "mochi" / "update"
    source_package.mkdir(parents=True)
    (source_package / "__init__.py").write_text("", encoding="utf-8")
    (source_package / "worker.py").write_text("VALUE = 1\n", encoding="utf-8")

    asset_root = tmp_path / "share" / "mochi"
    for name in ("idle", "wave", "sad_idle", "focus", "coffee"):
        directory = asset_root / name
        directory.mkdir(parents=True)
        (directory / "frame.png").write_bytes(b"png")
    (asset_root / "manifest.json").write_text(
        json.dumps(
            {
                "animations": {
                    "wave": {
                        "spritesheet": "wave/wave.png",
                        "frame_count": 8,
                        "source_cell_size": [64, 64],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    calls: list[tuple[list[str], dict[str, str]]] = []

    def popen(args, *, env):
        calls.append(([str(value) for value in args], dict(env)))
        return _Process()

    app_home = tmp_path / "app"
    process = bootstrap_updater(
        _target(),
        gui=True,
        wait_pid=4321,
        app_home=app_home,
        source_package=source_package,
        asset_root=asset_root,
        base_python=Path("/usr/bin/python3"),
        popen=popen,
    )

    assert isinstance(process, _Process)
    workspaces = list((app_home / "update-bootstrap").iterdir())
    assert len(workspaces) == 1
    workspace = workspaces[0]
    assert (workspace / "mochi" / "update" / "worker.py").exists()
    for name in ("idle", "wave", "sad_idle", "focus"):
        assert (workspace / "assets" / "mochi" / name / "frame.png").exists()
    assert (workspace / "assets" / "mochi" / "manifest.json").exists()
    assert not (workspace / "assets" / "mochi" / "coffee").exists()

    request = json.loads((workspace / "request.json").read_text(encoding="utf-8"))
    assert request == {
        "commit": "abc123",
        "version": "0.4.0a1",
        "channel": "main",
        "highlights": ["one", "two"],
        "wait_pid": 4321,
        "gui": True,
    }

    command, env = calls[0]
    assert Path(command[0]).as_posix() == "/usr/bin/python3"
    assert command[1] == str(workspace / "runner.py")
    assert env["MOCHI_UPDATER_ASSET_ROOT"] == str(workspace / "assets" / "mochi")
    # An exported PYTHONPATH reaches install.sh and the relaunched Mochi, where
    # the workspace's stub ``mochi`` package shadows the real one (0.4.0a1).
    assert "PYTHONPATH" not in env
    assert "MOCHI_UPDATER_WORKSPACE" not in env

    # The copied worker remains available even if the original package vanishes.
    renamed = source_package.with_name("update.removed")
    source_package.rename(renamed)
    assert (workspace / "mochi" / "update" / "worker.py").exists()


def test_bootstrap_never_copies_user_configuration(tmp_path: Path) -> None:
    source_package = tmp_path / "source" / "mochi" / "update"
    source_package.mkdir(parents=True)
    (source_package / "__init__.py").write_text("", encoding="utf-8")
    (source_package / "worker.py").write_text("", encoding="utf-8")

    asset_root = tmp_path / "share" / "mochi"
    for name in ("idle", "wave", "sad_idle", "focus"):
        (asset_root / name).mkdir(parents=True)

    user_config = tmp_path / "home" / ".config" / "mochi" / "config.json"
    user_config.parent.mkdir(parents=True)
    user_config.write_text('{"bond_xp": 999}\n', encoding="utf-8")

    bootstrap_updater(
        _target(),
        gui=False,
        wait_pid=None,
        app_home=tmp_path / "app",
        source_package=source_package,
        asset_root=asset_root,
        base_python=Path("/usr/bin/python3"),
        popen=lambda _args, *, env: _Process(),
    )

    workspace = next((tmp_path / "app" / "update-bootstrap").iterdir())
    copied_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in workspace.rglob("*")
        if path.is_file()
    )
    assert "bond_xp" not in copied_text
    assert "999" not in copied_text


def test_bootstrap_prefers_base_interpreter_over_replaceable_venv(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_package = tmp_path / "source" / "mochi" / "update"
    source_package.mkdir(parents=True)
    (source_package / "__init__.py").write_text("", encoding="utf-8")
    (source_package / "worker.py").write_text("", encoding="utf-8")
    asset_root = tmp_path / "share" / "mochi"
    for name in ("idle", "wave", "sad_idle", "focus"):
        (asset_root / name).mkdir(parents=True)

    commands: list[list[str]] = []
    fake_base = tmp_path / "system-python"
    fake_base.write_text("#!/bin/sh\n", encoding="utf-8")
    fake_base.chmod(0o755)
    monkeypatch.setattr(sys, "_base_executable", str(fake_base), raising=False)

    bootstrap_updater(
        _target(),
        gui=False,
        app_home=tmp_path / "app",
        source_package=source_package,
        asset_root=asset_root,
        popen=lambda args, *, env: commands.append(
            [str(value) for value in args]
        )
        or _Process(),
    )

    assert commands[0][0] == str(fake_base)
    assert "venv" not in commands[0][0]


def test_bootstrap_runner_honors_gui_flag_without_resolving_main_again() -> None:
    source = _runner_source()

    assert 'request.get("gui")' in source
    assert "run_external_update" in source
    assert "UpdateChecker" not in source
    assert "resolve_main_sha" not in source


def test_bootstrap_runner_cleans_workspace_after_updater_exits() -> None:
    source = _runner_source()

    assert "shutil.rmtree(workspace" in source
    assert "finally:" in source


def test_real_bootstrap_package_imports_without_rest_of_mochi(tmp_path: Path) -> None:
    source_package = Path(__file__).resolve().parents[1] / "src" / "mochi" / "update"
    asset_root = tmp_path / "share" / "mochi"
    for name in ("idle", "wave", "sad_idle", "focus"):
        (asset_root / name).mkdir(parents=True)

    app_home = tmp_path / "app"
    bootstrap_updater(
        _target(),
        gui=True,
        wait_pid=4321,
        app_home=app_home,
        source_package=source_package,
        asset_root=asset_root,
        base_python=Path(sys._base_executable or sys.executable),
        popen=lambda _args, *, env: _Process(),
    )

    workspace = next((app_home / "update-bootstrap").iterdir())
    # Copy cross-platform pure-Python cairo/gi fallback shims if present in src/
    src_root = Path(__file__).resolve().parents[1] / "src"
    if (src_root / "cairo.py").is_file():
        import shutil as _shutil
        _shutil.copy2(src_root / "cairo.py", workspace / "cairo.py")
    if (src_root / "gi").is_dir():
        import shutil as _shutil
        _shutil.copytree(src_root / "gi", workspace / "gi", dirs_exist_ok=True)
    runner_source = (workspace / "runner.py").read_text(encoding="utf-8")
    import_prelude = runner_source.split("request_path =", 1)[0]
    probe = workspace / "probe.py"
    probe.write_text(import_prelude + "print('ok')\n", encoding="utf-8")
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)

    # Run the runner's own import prelude the way bootstrap launches it: no
    # PYTHONPATH and an unrelated working directory.
    completed = subprocess.run(
        [sys._base_executable or sys.executable, str(probe)],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "ok"
