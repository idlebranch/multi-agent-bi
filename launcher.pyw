"""Windows GUI launcher for the Docker Compose Multi-Agent BI deployment."""

from __future__ import annotations

import ctypes
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk


APP_URL = "http://127.0.0.1:8000/"
HEALTH_URL = "http://127.0.0.1:8000/health"
APP_IMAGE = "multi-agent-bi-app:local"
CREATE_NO_WINDOW = 0x08000000
DOCKER_READY_TIMEOUT_SECONDS = 120.0
COMPOSE_TIMEOUT_SECONDS = 600.0
APP_READY_TIMEOUT_SECONDS = 180.0
POLL_INTERVAL_SECONDS = 2.0
BASE_DPI = 96
PER_MONITOR_AWARE_V2 = -4
REQUIRED_COMPOSE_KEYS = (
    "BI_POSTGRES_OWNER_PASSWORD",
    "BI_POSTGRES_READONLY_PASSWORD",
)


class LauncherError(RuntimeError):
    """A user-actionable launcher failure."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def enable_windows_dpi_awareness() -> str:
    """Enable native DPI handling before Tk creates any HWND."""
    if sys.platform != "win32":
        return "not_windows"

    user32 = ctypes.windll.user32
    try:
        set_context = user32.SetProcessDpiAwarenessContext
        set_context.argtypes = [ctypes.c_void_p]
        set_context.restype = ctypes.c_bool
        if set_context(ctypes.c_void_p(PER_MONITOR_AWARE_V2)):
            return "per_monitor_v2"
    except (AttributeError, OSError, TypeError, ValueError):
        pass

    try:
        shcore = ctypes.windll.shcore
        set_awareness = shcore.SetProcessDpiAwareness
        set_awareness.argtypes = [ctypes.c_int]
        set_awareness.restype = ctypes.c_long
        if set_awareness(2) == 0:
            return "per_monitor"
    except (AttributeError, OSError, TypeError, ValueError):
        pass

    try:
        set_aware = user32.SetProcessDPIAware
        set_aware.argtypes = []
        set_aware.restype = ctypes.c_bool
        if set_aware():
            return "system_aware"
    except (AttributeError, OSError, TypeError, ValueError):
        pass
    return "unchanged"


def get_window_dpi(root: tk.Tk) -> int:
    """Return the actual monitor DPI for a DPI-aware Tk window."""
    if sys.platform == "win32":
        try:
            get_dpi = ctypes.windll.user32.GetDpiForWindow
            get_dpi.argtypes = [ctypes.c_void_p]
            get_dpi.restype = ctypes.c_uint
            dpi = int(get_dpi(root.winfo_id()))
            if dpi > 0:
                return dpi
        except (AttributeError, OSError, TypeError, ValueError, tk.TclError):
            pass
    try:
        return max(1, round(float(root.winfo_fpixels("1i"))))
    except (TypeError, ValueError, tk.TclError):
        return BASE_DPI


def resolve_ui_font(root: tk.Tk) -> str:
    """Choose a native Windows UI font for every launcher widget."""
    families = {str(name).casefold(): str(name) for name in tkfont.families(root)}
    for candidate in ("Microsoft YaHei UI", "Segoe UI"):
        if candidate.casefold() in families:
            return families[candidate.casefold()]
    return "Segoe UI"


def find_project_root() -> Path:
    """Find the stable repository root relative to this script or frozen EXE."""
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        candidates.extend((executable_dir, executable_dir.parent))
    candidates.append(Path(__file__).resolve().parent)
    for candidate in candidates:
        if (candidate / "compose.yaml").is_file() and (
            candidate / "pyproject.toml"
        ).is_file():
            return candidate
    raise LauncherError(
        "Startup failed: repository root not found. Keep the launcher in the "
        "repository dist directory."
    )


PROJECT_ROOT = find_project_root()
LOG_DIR = PROJECT_ROOT / "logs"
LAUNCHER_LOG = LOG_DIR / "launcher.log"


def configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LAUNCHER_LOG, encoding="utf-8")],
    )


def redact(text: str) -> str:
    """Remove credential-shaped values before command output reaches the log."""
    text = re.sub(r"(postgres(?:ql)?://[^:\s]+:)[^@\s]+@", r"\1***@", text)
    return re.sub(
        r"(?i)((?:password|token|api[_-]?key|authorization)\s*[=:]\s*)\S+",
        r"\1***",
        text,
    )


def command_label(command: Sequence[str]) -> str:
    """Return a safe command label without environment values or credentials."""
    words = [Path(command[0]).name]
    words.extend(str(part) for part in command[1:])
    return " ".join(words)


def run_command(
    command: Sequence[str],
    *,
    timeout: float = 30.0,
    cwd: Path = PROJECT_ROOT,
) -> CommandResult:
    """Run a hidden subprocess and capture bounded diagnostic output."""
    logging.info("command started: %s", command_label(command))
    try:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        logging.error("command timed out: %s", command_label(command))
        raise LauncherError(
            f"Startup failed: command timed out ({command_label(command)})."
        ) from exc
    except OSError as exc:
        logging.exception("command could not start: %s", command_label(command))
        raise LauncherError(f"Startup failed: {exc}") from exc
    result = CommandResult(completed.returncode, completed.stdout, completed.stderr)
    logging.info(
        "command finished: %s returncode=%s",
        command_label(command),
        result.returncode,
    )
    return result


def fetch_health(timeout: float = 3.0) -> dict[str, Any] | None:
    """Return health only when HTTP, API, and PostgreSQL are all ready."""
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=timeout) as response:
            status_code = getattr(response, "status", None)
            if status_code is None:
                status_code = response.getcode()
            if status_code != 200:
                return None
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError):
        return None
    database = payload.get("database") if isinstance(payload, dict) else None
    if (
        not isinstance(database, dict)
        or payload.get("service") != "bi-agent-api"
        or payload.get("status") != "ok"
        or database.get("status") != "ready"
    ):
        return None
    return payload


def open_app() -> None:
    logging.info("opening application url=%s", APP_URL)
    if not webbrowser.open(APP_URL):
        raise LauncherError(f"Startup failed: could not open the browser at {APP_URL}")


def _candidate_paths() -> tuple[list[Path], list[Path]]:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
    program_files = Path(os.environ.get("ProgramFiles", ""))
    docker_cli = [
        program_files / "Docker" / "Docker" / "resources" / "bin" / "docker.exe",
        local_app_data
        / "Programs"
        / "DockerDesktop"
        / "resources"
        / "bin"
        / "docker.exe",
    ]
    docker_desktop = [
        program_files / "Docker" / "Docker" / "Docker Desktop.exe",
        local_app_data
        / "Programs"
        / "DockerDesktop"
        / "Docker Desktop.exe",
    ]
    return docker_cli, docker_desktop


def find_docker_cli() -> Path:
    """Find Docker CLI from PATH or real per-machine/per-user installs."""
    on_path = shutil.which("docker")
    cli_candidates, _ = _candidate_paths()
    candidates = ([Path(on_path)] if on_path else []) + cli_candidates
    for candidate in candidates:
        if candidate.is_file():
            logging.info("Docker CLI detected path=%s", candidate)
            return candidate
    raise LauncherError("Docker Desktop is not installed.")


def find_docker_desktop() -> Path:
    """Find Docker Desktop without a user-specific hard-coded path."""
    _, candidates = _candidate_paths()
    for candidate in candidates:
        if candidate.is_file():
            logging.info("Docker Desktop detected path=%s", candidate)
            return candidate
    raise LauncherError("Docker Desktop is not installed.")


def docker_engine_ready(docker: Path) -> bool:
    result = run_command(
        [str(docker), "info", "--format", "{{.ServerVersion}}"], timeout=8.0
    )
    ready = result.returncode == 0 and bool(result.stdout.strip())
    logging.info("Docker Engine ready=%s", ready)
    return ready


def ensure_docker_ready(docker: Path, update: Callable[[str], None]) -> None:
    """Start Docker Desktop if needed and poll Engine readiness for 120 seconds."""
    if docker_engine_ready(docker):
        return
    desktop = find_docker_desktop()
    update("Docker Desktop is starting...")
    logging.info("starting Docker Desktop")
    try:
        subprocess.Popen(
            [str(desktop)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except OSError as exc:
        raise LauncherError(
            f"Startup failed: could not start Docker Desktop: {exc}"
        ) from exc

    deadline = time.monotonic() + DOCKER_READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL_SECONDS)
        if docker_engine_ready(docker):
            logging.info("Docker Engine became ready")
            return
    raise LauncherError(
        "Startup failed: Docker Engine was not ready within 120 seconds. "
        "Open Docker Desktop and check its status."
    )


def read_env(path: Path) -> dict[str, str]:
    """Read simple Compose dotenv keys without logging values."""
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return values
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def validate_compose_config(project_root: Path = PROJECT_ROOT) -> None:
    values = read_env(project_root / ".env")
    missing = [key for key in REQUIRED_COMPOSE_KEYS if not values.get(key)]
    if missing:
        logging.error("required Compose keys missing: %s", ", ".join(missing))
        raise LauncherError(
            "Startup failed: .env is missing required PostgreSQL settings: "
            + ", ".join(missing)
        )


def compose_command(docker: Path, *arguments: str) -> list[str]:
    return [str(docker), "compose", *arguments]


def has_existing_database_storage(docker: Path) -> bool:
    """Conservatively detect an existing Compose PostgreSQL container or volume."""
    container = run_command(
        compose_command(docker, "ps", "-a", "-q", "postgres"), timeout=20.0
    )
    if container.returncode == 0 and container.stdout.strip():
        return True
    project_name = re.sub(r"[^a-z0-9_-]", "", PROJECT_ROOT.name.casefold())
    volume_name = f"{project_name}_olist_postgres_data"
    volume = run_command(
        [str(docker), "volume", "inspect", volume_name], timeout=20.0
    )
    return volume.returncode == 0


def validate_first_run_data(docker: Path) -> None:
    source = PROJECT_ROOT / "data" / "raw" / "olist.zip"
    if source.is_file() or has_existing_database_storage(docker):
        return
    raise LauncherError(
        "Startup failed: first-time database initialization requires "
        f"{source}."
    )


def image_exists(docker: Path) -> bool:
    result = run_command(
        [str(docker), "image", "inspect", APP_IMAGE], timeout=20.0
    )
    return result.returncode == 0


def compose_output(result: CommandResult) -> str:
    return redact("\n".join(part for part in (result.stdout, result.stderr) if part))


def start_compose(docker: Path, update: Callable[[str], None]) -> None:
    """Start Compose, adding --build only when the local app image is absent."""
    if image_exists(docker):
        command = compose_command(docker, "up", "-d")
        logging.info("Compose startup reuses existing image")
    else:
        update("First run: building the application image...")
        command = compose_command(docker, "up", "-d", "--build")
        logging.info("application image absent; Compose build fallback enabled")
    result = run_command(command, timeout=COMPOSE_TIMEOUT_SECONDS)
    output = compose_output(result)
    if output:
        logging.info("Compose output:\n%s", output[-8000:])
    if result.returncode == 0:
        return
    source = PROJECT_ROOT / "data" / "raw" / "olist.zip"
    if not source.is_file():
        raise LauncherError(
            "Startup failed: PostgreSQL is empty and data/raw/olist.zip is missing."
        )
    raise LauncherError(
        "Startup failed: docker compose up failed. Check logs/launcher.log and "
        "Docker Desktop."
    )


def compose_diagnostics(docker: Path) -> str:
    result = run_command(
        compose_command(docker, "ps", "--format", "json"), timeout=20.0
    )
    if result.returncode != 0:
        return "compose ps unavailable"
    services: dict[str, str] = {}
    for line in result.stdout.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        service = str(item.get("Service", "unknown"))
        state = str(item.get("State", "unknown"))
        health = str(item.get("Health", ""))
        services[service] = "/".join(part for part in (state, health) if part)
    return ", ".join(f"{name}={state}" for name, state in sorted(services.items()))


def wait_for_application(docker: Path, update: Callable[[str], None]) -> None:
    update("Multi-Agent BI is starting...")
    deadline = time.monotonic() + APP_READY_TIMEOUT_SECONDS
    attempts = 0
    while time.monotonic() < deadline:
        attempts += 1
        if fetch_health():
            logging.info("health polling passed attempts=%s", attempts)
            return
        time.sleep(POLL_INTERVAL_SECONDS)
    diagnostics = compose_diagnostics(docker)
    logging.error("application health timeout diagnostics=%s", diagnostics)
    raise LauncherError(
        "Startup failed: /health was not ready within 180 seconds "
        f"({diagnostics}). Check the app, postgres, and loader status in Docker Desktop."
    )


def startup_workflow(update: Callable[[str], None]) -> None:
    """Execute the one-click path and open the real UI only after full readiness."""
    update("Checking Multi-Agent BI...")
    if fetch_health():
        logging.info("application already healthy; Compose startup skipped")
        update("Multi-Agent BI is ready.")
        open_app()
        return

    docker = find_docker_cli()
    ensure_docker_ready(docker, update)
    validate_compose_config()
    validate_first_run_data(docker)
    start_compose(docker, update)
    wait_for_application(docker, update)
    update("Multi-Agent BI is ready.")
    open_app()


class LauncherWindow:
    """Small progress window; normal success closes automatically."""

    def __init__(self, root: tk.Tk, *, auto_start: bool = True) -> None:
        self.root = root
        self.dpi = get_window_dpi(root)
        self.ui_scale = self.dpi / BASE_DPI
        self.ui_font = resolve_ui_font(root)
        root.title("Multi-Agent BI")
        root.geometry(
            f"{round(520 * self.ui_scale)}x{round(170 * self.ui_scale)}"
        )
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", root.destroy)
        self.status = tk.StringVar(value="Launcher ready.")

        for font_name in (
            "TkDefaultFont",
            "TkTextFont",
            "TkMenuFont",
            "TkHeadingFont",
            "TkCaptionFont",
            "TkSmallCaptionFont",
            "TkIconFont",
            "TkTooltipFont",
        ):
            try:
                tkfont.nametofont(font_name, root=root).configure(family=self.ui_font)
            except tk.TclError:
                continue

        style = ttk.Style(root)
        style.theme_use("vista")
        style.configure(".", font=(self.ui_font, 10))
        style.configure("Title.TLabel", font=(self.ui_font, 18, "bold"))

        frame = ttk.Frame(root, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Multi-Agent BI", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            frame,
            text="Docker Compose · FastAPI · PostgreSQL",
            foreground="#667085",
        ).pack(anchor="w", pady=(2, 18))
        ttk.Label(
            frame,
            textvariable=self.status,
            wraplength=round(460 * self.ui_scale),
        ).pack(anchor="w")
        progress = ttk.Progressbar(frame, mode="indeterminate")
        progress.pack(fill="x", pady=(14, 0))
        progress.start(12)

        if auto_start:
            root.after(100, self.start)

    def set_status(self, message: str) -> None:
        self.root.after(0, lambda: self.status.set(message))

    def start(self) -> None:
        def worker() -> None:
            try:
                startup_workflow(self.set_status)
            except Exception as exc:
                logging.exception("launcher startup failed")
                message = str(exc) or "Startup failed: unknown error."
                self.root.after(
                    0,
                    lambda: messagebox.showerror("Multi-Agent BI", message),
                )
                self.root.after(0, self.root.destroy)
                return
            self.root.after(500, self.root.destroy)

        threading.Thread(target=worker, daemon=True).start()


def main() -> None:
    dpi_mode = enable_windows_dpi_awareness()
    configure_logging()
    logging.info(
        "launcher opened project_root=%s dpi_awareness=%s",
        PROJECT_ROOT,
        dpi_mode,
    )
    root = tk.Tk()
    LauncherWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
