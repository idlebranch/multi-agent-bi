"""Unit coverage for the Windows Docker Compose launcher decision paths."""

from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def launcher() -> dict[str, Any]:
    return runpy.run_path(str(PROJECT_ROOT / "launcher.pyw"))


def test_healthy_fast_path_skips_docker_and_opens_app(
    launcher: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    globals_ = launcher["startup_workflow"].__globals__
    monkeypatch.setitem(globals_, "fetch_health", lambda: {"status": "ok"})
    monkeypatch.setitem(
        globals_,
        "find_docker_cli",
        lambda: pytest.fail("healthy fast path must not inspect Docker"),
    )
    monkeypatch.setitem(globals_, "open_app", lambda: calls.append("opened"))

    launcher["startup_workflow"](calls.append)

    assert calls[-2:] == ["Multi-Agent BI is ready.", "opened"]


def test_existing_image_uses_plain_compose_up(
    launcher: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []
    globals_ = launcher["start_compose"].__globals__
    monkeypatch.setitem(globals_, "image_exists", lambda _docker: True)

    def fake_run(command: list[str], **_kwargs: Any) -> Any:
        commands.append(command)
        return launcher["CommandResult"](0)

    monkeypatch.setitem(globals_, "run_command", fake_run)
    launcher["start_compose"](Path("docker.exe"), lambda _message: None)

    assert commands == [["docker.exe", "compose", "up", "-d"]]


def test_missing_image_enables_build_fallback(
    launcher: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []
    statuses: list[str] = []
    globals_ = launcher["start_compose"].__globals__
    monkeypatch.setitem(globals_, "image_exists", lambda _docker: False)

    def fake_run(command: list[str], **_kwargs: Any) -> Any:
        commands.append(command)
        return launcher["CommandResult"](0)

    monkeypatch.setitem(globals_, "run_command", fake_run)
    launcher["start_compose"](Path("docker.exe"), statuses.append)

    assert commands == [["docker.exe", "compose", "up", "-d", "--build"]]
    assert statuses == ["First run: building the application image..."]


def test_compose_config_requires_both_postgres_passwords(
    launcher: dict[str, Any], tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text(
        "BI_POSTGRES_OWNER_PASSWORD=present\n", encoding="utf-8"
    )

    with pytest.raises(launcher["LauncherError"], match="READONLY_PASSWORD"):
        launcher["validate_compose_config"](tmp_path)


def test_docker_desktop_start_is_polled_until_engine_ready(
    launcher: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    readiness = iter((False, False, True))
    sleeps: list[float] = []
    starts: list[list[str]] = []
    statuses: list[str] = []
    globals_ = launcher["ensure_docker_ready"].__globals__
    monkeypatch.setitem(
        globals_, "docker_engine_ready", lambda _docker: next(readiness)
    )
    monkeypatch.setitem(
        globals_, "find_docker_desktop", lambda: Path("Docker Desktop.exe")
    )
    monkeypatch.setattr(globals_["time"], "sleep", sleeps.append)
    monkeypatch.setattr(
        globals_["subprocess"],
        "Popen",
        lambda command, **_kwargs: starts.append(command),
    )

    launcher["ensure_docker_ready"](Path("docker.exe"), statuses.append)

    assert starts == [["Docker Desktop.exe"]]
    assert sleeps == [2.0, 2.0]
    assert statuses == ["Docker Desktop is starting..."]


def test_first_run_without_volume_or_olist_zip_fails_clearly(
    launcher: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    globals_ = launcher["validate_first_run_data"].__globals__
    monkeypatch.setitem(globals_, "PROJECT_ROOT", tmp_path)
    monkeypatch.setitem(
        globals_, "has_existing_database_storage", lambda _docker: False
    )

    with pytest.raises(launcher["LauncherError"], match="olist.zip"):
        launcher["validate_first_run_data"](Path("docker.exe"))
