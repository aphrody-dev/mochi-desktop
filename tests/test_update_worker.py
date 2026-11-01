"""Transactional updater and rollback regression coverage."""

from __future__ import annotations

import io
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import sys
import threading
import time

from mochi.update.model import InstalledBuild, UpdateMetadata, UpdateTarget
from mochi.update.storage import InstallMetadataStore
from mochi.update.worker import (
    CANDIDATE_CHECK_SOURCE,
    READY_TIMEOUT_SECONDS,
    UpdatePaths,
    UpdateStage,
    UpdateWorker,
    child_environment,
)


def _target(commit: str = "abc123") -> UpdateTarget:
    return UpdateTarget(
        commit=commit,
        metadata=UpdateMetadata(
            version="0.4.0a1",
            channel="main",
            highlights=("new tricks",),
        ),
    )


def _make_source_archive(path: Path, *, unsafe: bool = False) -> None:
    with tarfile.open(path, "w:gz") as archive:
        if unsafe:
            info = tarfile.TarInfo("../../escaped.txt")
            payload = b"nope"
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
            return

        root = "mochi-desktop-abc123"
        files = {
            f"{root}/pyproject.toml": b"[project]\nname='mochi-desktop'\n",
            f"{root}/install.sh": b"#!/bin/bash\nexit 0\n",
            f"{root}/src/mochi/__init__.py": b"",
            f"{root}/assets/mochi/manifest.json": b"{}\n",
        }
        for name, payload in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = 0o755 if name.endswith("install.sh") else 0o644
            archive.addfile(info, io.BytesIO(payload))


class _Harness:
    def __init__(
        self,
        tmp_path: Path,
        *,
        stage_returncode: int = 0,
        refresh_returncode: int = 0,
        ready: bool = True,
        validation_returncode: int = 0,
    ) -> None:
        self.archive = tmp_path / "source.tar.gz"
        _make_source_archive(self.archive)
        self.stage_returncode = stage_returncode
        self.refresh_returncode = refresh_returncode
        self.ready = ready
        self.validation_returncode = validation_returncode
        self.child_environments: list[tuple[str, dict[str, str] | None]] = []
        self.download_urls: list[str] = []
        self.commands: list[tuple[str, ...]] = []
        self.launches: list[tuple[str, ...]] = []
        self.waited_pids: list[int] = []
        self.ready_timeouts: list[float] = []
        self.lifecycle: list[str] = []

    def download(self, url: str, destination: Path, on_bytes) -> None:
        self.download_urls.append(url)
        shutil.copy2(self.archive, destination)
        size = destination.stat().st_size
        on_bytes(size, size)

    def run_command(self, args, *, cwd=None, env=None):
        argv = tuple(str(value) for value in args)
        self.commands.append(argv)
        self.child_environments.append((argv[0], env))

        if "--stage-runtime" in argv:
            self.lifecycle.append("stage")
            target = Path(argv[argv.index("--stage-runtime") + 1])
            if self.stage_returncode == 0:
                (target / "bin").mkdir(parents=True, exist_ok=True)
                for name in ("python", "mochi"):
                    path = target / "bin" / name
                    path.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
                    path.chmod(0o755)
                (target / "share" / "mochi" / "master").mkdir(
                    parents=True, exist_ok=True
                )
                (target / "share" / "mochi" / "manifest.json").write_text(
                    "{}\n", encoding="utf-8"
                )
                (target / "share" / "mochi" / "master" / "mochi_default.png").touch()
            return subprocess.CompletedProcess(
                list(argv),
                self.stage_returncode,
                stdout="Installing Mochi\n",
                stderr="" if self.stage_returncode == 0 else "pip: build failed\n",
            )

        if "--refresh-integrations" in argv:
            return subprocess.CompletedProcess(
                list(argv),
                self.refresh_returncode,
                stdout="",
                stderr="" if self.refresh_returncode == 0 else "icon cache failed\n",
            )

        # Candidate validation commands.
        if self.validation_returncode != 0:
            return subprocess.CompletedProcess(
                list(argv),
                self.validation_returncode,
                stdout="",
                stderr="ModuleNotFoundError: No module named 'mochi.main'\n",
            )
        return subprocess.CompletedProcess(list(argv), 0, stdout="0.4.0a1\n", stderr="")

    def launch(self, args, *, env=None):
        argv = tuple(str(value) for value in args)
        self.launches.append(argv)
        self.child_environments.append((argv[0], env))
        return object()

    def wait_for_pid(self, pid: int) -> None:
        self.lifecycle.append("wait")
        self.waited_pids.append(pid)

    def wait_for_ready(self, path: Path, timeout_seconds: float) -> bool:
        self.ready_timeouts.append(timeout_seconds)
        if self.ready:
            path.write_text("ready\n", encoding="utf-8")
        return self.ready


def _paths(tmp_path: Path) -> UpdatePaths:
    app_home = tmp_path / "data" / "mochi-desktop"
    final = app_home / "venv"
    (final / "bin").mkdir(parents=True)
    old_mochi = final / "bin" / "mochi"
    old_mochi.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    old_mochi.chmod(0o755)
    (final / "old.marker").write_text("old", encoding="utf-8")
    return UpdatePaths.for_environment(
        data_home=tmp_path / "data",
        temp_root=tmp_path / "work",
    )


def _worker(
    tmp_path: Path,
    harness: _Harness,
    paths: UpdatePaths,
) -> tuple[UpdateWorker, InstallMetadataStore]:
    store = InstallMetadataStore(paths.app_home / "install.json")
    return (
        UpdateWorker(
            paths=paths,
            install_store=store,
            download_archive=harness.download,
            run_command=harness.run_command,
            launch_command=harness.launch,
            wait_for_pid=harness.wait_for_pid,
            wait_for_ready=harness.wait_for_ready,
            now_iso=lambda: "2026-09-26T13:00:00+00:00",
        ),
        store,
    )


def test_update_download_is_pinned_to_exact_target_sha(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    harness = _Harness(tmp_path)
    worker, _store = _worker(tmp_path, harness, paths)

    assert worker.run(_target("deadbeef"), wait_pid=None, on_progress=lambda _p: None) == 0

    assert harness.download_urls == [
        "https://codeload.github.com/miflow13/mochi-desktop/tar.gz/deadbeef"
    ]
    assert not any("/commits/main" in url for url in harness.download_urls)


def test_unsafe_archive_is_rejected_before_install(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    harness = _Harness(tmp_path)
    _make_source_archive(harness.archive, unsafe=True)
    worker, _store = _worker(tmp_path, harness, paths)

    assert worker.run(_target(), wait_pid=None, on_progress=lambda _p: None) != 0

    assert harness.commands == []
    assert (paths.final_venv / "old.marker").read_text(encoding="utf-8") == "old"
    assert not (tmp_path / "escaped.txt").exists()


def test_stage_failure_leaves_current_runtime_untouched(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    harness = _Harness(tmp_path, stage_returncode=42)
    worker, _store = _worker(tmp_path, harness, paths)

    assert worker.run(_target(), wait_pid=None, on_progress=lambda _p: None) == 42

    assert (paths.final_venv / "old.marker").read_text(encoding="utf-8") == "old"
    assert not paths.update_venv.exists()
    assert not paths.backup_venv.exists()


def test_worker_waits_then_builds_candidate_at_final_runtime_path(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    harness = _Harness(tmp_path)
    worker, _store = _worker(tmp_path, harness, paths)

    assert worker.run(_target(), wait_pid=4321, on_progress=lambda _p: None) == 0

    assert harness.waited_pids == [4321]
    assert harness.lifecycle[:2] == ["wait", "stage"]

    stage_command = next(
        command for command in harness.commands if "--stage-runtime" in command
    )
    target = Path(stage_command[stage_command.index("--stage-runtime") + 1])
    assert target == paths.final_venv
    assert target != paths.update_venv


def test_successful_swap_waits_for_readiness_then_removes_backup_and_records_target(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    harness = _Harness(tmp_path, ready=True)
    worker, store = _worker(tmp_path, harness, paths)

    assert worker.run(_target("good-sha"), wait_pid=123, on_progress=lambda _p: None) == 0

    assert not paths.backup_venv.exists()
    assert not paths.update_venv.exists()
    assert not (paths.final_venv / "old.marker").exists()
    build = store.load()
    assert build == InstalledBuild(
        version="0.4.0a1",
        commit="good-sha",
        channel="main",
        installed_at="2026-09-26T13:00:00+00:00",
    )
    assert harness.ready_timeouts == [READY_TIMEOUT_SECONDS]
    assert any("--update-ready-file" in launch for launch in harness.launches)


def test_refresh_failure_rolls_back_previous_runtime(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    harness = _Harness(tmp_path, refresh_returncode=9)
    worker, _store = _worker(tmp_path, harness, paths)

    assert worker.run(_target(), wait_pid=None, on_progress=lambda _p: None) == 9

    assert (paths.final_venv / "old.marker").read_text(encoding="utf-8") == "old"
    assert not paths.backup_venv.exists()
    assert Path(harness.launches[-1][0]).as_posix().endswith("/venv/bin/mochi")


def test_readiness_timeout_restores_previous_runtime_and_relaunches_it(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    harness = _Harness(tmp_path, ready=False)
    worker, _store = _worker(tmp_path, harness, paths)

    assert worker.run(_target(), wait_pid=None, on_progress=lambda _p: None) != 0

    assert (paths.final_venv / "old.marker").read_text(encoding="utf-8") == "old"
    assert not paths.backup_venv.exists()
    assert harness.ready_timeouts == [READY_TIMEOUT_SECONDS]
    assert Path(harness.launches[-1][0]).as_posix().endswith("/venv/bin/mochi")


def test_stale_backup_restores_when_final_runtime_is_missing(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    shutil.rmtree(paths.final_venv)
    (paths.backup_venv / "bin").mkdir(parents=True)
    (paths.backup_venv / "old.marker").write_text("recovered", encoding="utf-8")
    old_mochi = paths.backup_venv / "bin" / "mochi"
    old_mochi.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    old_mochi.chmod(0o755)

    harness = _Harness(tmp_path)
    worker, _store = _worker(tmp_path, harness, paths)

    assert worker.run(_target(), wait_pid=None, on_progress=lambda _p: None) == 0

    assert not paths.backup_venv.exists()
    assert not paths.update_venv.exists()


def test_recovery_prefers_known_good_backup_over_partial_final_runtime(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    shutil.rmtree(paths.final_venv)
    (paths.backup_venv / "bin").mkdir(parents=True)
    (paths.backup_venv / "old.marker").write_text("known-good", encoding="utf-8")
    old_mochi = paths.backup_venv / "bin" / "mochi"
    old_mochi.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    old_mochi.chmod(0o755)

    (paths.final_venv / "bin").mkdir(parents=True)
    (paths.final_venv / "partial.marker").write_text("partial", encoding="utf-8")

    harness = _Harness(tmp_path)
    worker, _store = _worker(tmp_path, harness, paths)

    worker._recover_stale_paths()

    assert (paths.final_venv / "old.marker").read_text(encoding="utf-8") == "known-good"
    assert not (paths.final_venv / "partial.marker").exists()
    assert not paths.backup_venv.exists()


def test_failure_cleans_temp_workspace_and_ready_file(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    harness = _Harness(tmp_path, stage_returncode=3)
    worker, _store = _worker(tmp_path, harness, paths)

    assert worker.run(_target(), wait_pid=None, on_progress=lambda _p: None) == 3

    assert not paths.ready_file.exists()
    assert not paths.update_venv.exists()
    assert paths.temp_root.exists()
    assert list(paths.temp_root.iterdir()) == []


def test_cancel_before_swap_keeps_current_runtime_and_skips_install(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    harness = _Harness(tmp_path)
    worker, _store = _worker(tmp_path, harness, paths)
    cancel = threading.Event()
    cancel.set()

    result = worker.run(
        _target(),
        wait_pid=None,
        on_progress=lambda _p: None,
        cancel_event=cancel,
    )

    assert result == 130
    assert (paths.final_venv / "old.marker").read_text(encoding="utf-8") == "old"
    assert harness.commands == []
    assert not paths.backup_venv.exists()


def test_default_pid_wait_has_a_bounded_timeout() -> None:
    import mochi.update.worker as worker_module

    assert hasattr(worker_module, "PID_WAIT_TIMEOUT_SECONDS")
    assert 1 <= worker_module.PID_WAIT_TIMEOUT_SECONDS <= 60


def test_archive_extraction_uses_data_filter() -> None:
    source = __import__("inspect").getsource(UpdateWorker._extract_and_validate_source)
    assert 'filter="data"' in source


def test_worker_never_promotes_a_built_virtualenv_by_renaming_it(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    harness = _Harness(tmp_path)
    worker, _store = _worker(tmp_path, harness, paths)

    assert worker.run(_target(), wait_pid=None, on_progress=lambda _p: None) == 0

    stage_command = next(
        command for command in harness.commands if "--stage-runtime" in command
    )
    assert str(paths.final_venv) in stage_command
    assert str(paths.update_venv) not in stage_command


def _bootstrap_environment(monkeypatch, tmp_path: Path) -> Path:
    """Reproduce the environment the 0.4.0a1 bootstrap gave the worker."""
    workspace = tmp_path / "update-bootstrap" / "update-abc"
    monkeypatch.setenv("PYTHONPATH", str(workspace))
    monkeypatch.setenv("MOCHI_UPDATER_WORKSPACE", str(workspace))
    monkeypatch.setenv("MOCHI_UPDATER_ASSET_ROOT", str(workspace / "assets" / "mochi"))
    monkeypatch.setenv("MOCHI_UNRELATED_SETTING", "kept")
    return workspace


def _assert_clean_child_environment(env: dict[str, str] | None) -> None:
    assert env is not None, "children must get an explicit, sanitized environment"
    assert "PYTHONPATH" not in env
    assert not [key for key in env if key.startswith("MOCHI_UPDATER_")]
    assert env["MOCHI_UNRELATED_SETTING"] == "kept"


def test_every_child_process_gets_an_environment_without_updater_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _bootstrap_environment(monkeypatch, tmp_path)
    paths = _paths(tmp_path)
    harness = _Harness(tmp_path)
    worker, _store = _worker(tmp_path, harness, paths)

    assert worker.run(_target("good-sha"), wait_pid=None, on_progress=lambda _p: None) == 0

    executables = [Path(name).name for name, _env in harness.child_environments]
    assert executables.count("install.sh") == 2
    assert "python" in executables
    assert "mochi" in executables
    for _name, env in harness.child_environments:
        _assert_clean_child_environment(env)
    installer_envs = [
        env for name, env in harness.child_environments if name.endswith("install.sh")
    ]
    assert all(env["MOCHI_INSTALLED_COMMIT"] == "good-sha" for env in installer_envs)


def test_rollback_relaunch_also_gets_a_clean_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _bootstrap_environment(monkeypatch, tmp_path)
    paths = _paths(tmp_path)
    harness = _Harness(tmp_path, ready=False)
    worker, _store = _worker(tmp_path, harness, paths)

    assert worker.run(_target(), wait_pid=None, on_progress=lambda _p: None) != 0

    assert len(harness.launches) == 2
    launch_envs = [
        env
        for name, env in harness.child_environments
        if Path(name).as_posix().endswith("/bin/mochi")
    ]
    assert len(launch_envs) == 2
    for env in launch_envs:
        _assert_clean_child_environment(env)


def test_child_environment_drops_any_pythonpath_and_updater_variables() -> None:
    environment = child_environment(
        {
            "PATH": "/usr/bin",
            "PYTHONPATH": "/home/me/dev/src",
            "MOCHI_UPDATER_ASSET_ROOT": "/tmp/assets",
            "MOCHI_INSTALLED_COMMIT": "abc",
        }
    )

    assert environment == {"PATH": "/usr/bin", "MOCHI_INSTALLED_COMMIT": "abc"}


def _failure_messages(events: list) -> list[str]:
    return [event.message for event in events if event.stage is UpdateStage.FAILED]


def test_stage_failure_reports_installer_output(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    harness = _Harness(tmp_path, stage_returncode=42)
    worker, _store = _worker(tmp_path, harness, paths)
    events: list = []

    assert worker.run(_target(), wait_pid=None, on_progress=events.append) == 42

    [message] = _failure_messages(events)
    assert "previous version was restored" in message
    assert "pip: build failed" in message
    assert "Installing Mochi" in message


def test_refresh_failure_reports_installer_output(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    harness = _Harness(tmp_path, refresh_returncode=9)
    worker, _store = _worker(tmp_path, harness, paths)
    events: list = []

    assert worker.run(_target(), wait_pid=None, on_progress=events.append) == 9

    [message] = _failure_messages(events)
    assert "desktop integration" in message
    assert "icon cache failed" in message


def test_readiness_timeout_reports_failure_instead_of_exiting_silently(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    harness = _Harness(tmp_path, ready=False)
    worker, _store = _worker(tmp_path, harness, paths)
    events: list = []

    assert worker.run(_target(), wait_pid=None, on_progress=events.append) == 1

    [message] = _failure_messages(events)
    assert "didn't start" in message
    assert "previous version was restored" in message


def test_candidate_check_failure_rolls_back_and_reports_why(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    harness = _Harness(tmp_path, validation_returncode=1)
    worker, store = _worker(tmp_path, harness, paths)
    events: list = []

    assert worker.run(_target(), wait_pid=None, on_progress=events.append) != 0

    [message] = _failure_messages(events)
    assert "startup check" in message
    assert "No module named 'mochi.main'" in message
    assert (paths.final_venv / "old.marker").read_text(encoding="utf-8") == "old"
    assert Path(harness.launches[-1][0]).as_posix().endswith("/venv/bin/mochi")
    assert store.load() is None


def _run_candidate_check(pythonpath: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = pythonpath
    return subprocess.run(
        [sys.executable, "-c", CANDIDATE_CHECK_SOURCE],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def test_candidate_check_rejects_the_updater_stub_package(tmp_path: Path) -> None:
    stub = tmp_path / "workspace"
    (stub / "mochi" / "update").mkdir(parents=True)
    (stub / "mochi" / "__init__.py").write_text("", encoding="utf-8")
    (stub / "mochi" / "update" / "__init__.py").write_text("", encoding="utf-8")

    completed = _run_candidate_check(str(stub))

    # The 0.4.0a1 check ran only ``import mochi`` and passed on this stub.
    assert completed.returncode != 0
    assert "mochi.main" in completed.stderr


def test_candidate_check_rejects_mochi_imported_from_outside_the_runtime() -> None:
    source_tree = Path(__file__).resolve().parents[1] / "src"

    completed = _run_candidate_check(str(source_tree))

    assert completed.returncode != 0
    assert "outside the candidate runtime" in completed.stderr
