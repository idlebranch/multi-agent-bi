"""Small Windows GUI launcher for the local Multi-Agent BI demo."""

from __future__ import annotations

import ctypes
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk


APP_URL = "http://127.0.0.1:8000"
HEALTH_URL = f"{APP_URL}/health"
DOCS_URL = f"{APP_URL}/docs"
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
START_TIMEOUT_SECONDS = 45.0
BASE_DPI = 96
PER_MONITOR_AWARE_V2 = -4


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
    """Choose one native Windows UI font for every launcher widget."""
    families = {str(name).casefold(): str(name) for name in tkfont.families(root)}
    for candidate in ("Microsoft YaHei UI", "Segoe UI"):
        if candidate.casefold() in families:
            return families[candidate.casefold()]
    return "Segoe UI"


def find_project_root() -> Path:
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        candidates.extend((executable_dir, executable_dir.parent))
    candidates.append(Path(__file__).resolve().parent)
    for candidate in candidates:
        if (candidate / "api.py").is_file() and (candidate / "pyproject.toml").is_file():
            return candidate
    raise FileNotFoundError(
        "找不到项目根目录。请将启动器保留在项目的 dist 目录中。"
    )


PROJECT_ROOT = find_project_root()
LOG_DIR = PROJECT_ROOT / "logs"
LAUNCHER_LOG = LOG_DIR / "launcher.log"
SERVER_LOG = LOG_DIR / "launcher_server.log"
PID_FILE = PROJECT_ROOT / ".agent_server.pid"


def configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LAUNCHER_LOG, encoding="utf-8")],
    )


def fetch_health(timeout: float = 2.5) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return None
    if payload.get("service") != "bi-agent-api":
        return None
    return payload


def port_is_open(host: str = "127.0.0.1", port: int = 8000) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.8):
            return True
    except OSError:
        return False


def find_start_command() -> list[str]:
    uv = shutil.which("uv")
    if uv:
        return [uv, "run", "python", "api.py"]
    project_python = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    if project_python.is_file():
        return [str(project_python), "api.py"]
    raise FileNotFoundError(
        "未找到 uv 或项目 Python。请先在项目目录运行 uv sync --locked。"
    )


def write_pid_file(process: subprocess.Popen[Any], command: list[str]) -> None:
    payload = {
        "pid": process.pid,
        "project_root": str(PROJECT_ROOT),
        "command": command,
        "created_at_epoch": time.time(),
    }
    PID_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def read_pid_file() -> dict[str, Any] | None:
    try:
        payload = json.loads(PID_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("pid"), int):
        return None
    return payload


def remove_pid_file() -> None:
    try:
        PID_FILE.unlink(missing_ok=True)
    except OSError:
        logging.exception("failed to remove PID file")


def get_process_info(pid: int) -> dict[str, Any] | None:
    if pid <= 0:
        return None
    command = (
        "$p=Get-CimInstance Win32_Process -Filter \"ProcessId = "
        f"{pid}\" -ErrorAction SilentlyContinue;"
        "if($p){$p|Select-Object ProcessId,ExecutablePath,CommandLine|ConvertTo-Json -Compress}"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=CREATE_NO_WINDOW,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        payload = json.loads(result.stdout)
        return payload if isinstance(payload, dict) else None
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None


def pid_matches_project(payload: dict[str, Any]) -> bool:
    try:
        pid = int(payload["pid"])
    except (KeyError, TypeError, ValueError):
        return False
    if Path(str(payload.get("project_root", ""))).resolve() != PROJECT_ROOT:
        return False
    info = get_process_info(pid)
    if not info:
        return False
    command_line = str(info.get("CommandLine") or "").casefold()
    saved_command = payload.get("command") or []
    saved_executable = Path(str(saved_command[0])).resolve() if saved_command else None
    actual_executable = str(info.get("ExecutablePath") or "")
    executable_matches = bool(
        saved_executable
        and actual_executable
        and Path(actual_executable).resolve() == saved_executable
    )
    return executable_matches and "api.py" in command_line


def start_server_process() -> subprocess.Popen[Any]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    command = find_start_command()
    log_handle = SERVER_LOG.open("a", encoding="utf-8", buffering=1)
    logging.info("starting server command=%s", command[0:2] + ["api.py"])
    try:
        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
        )
    except Exception:
        log_handle.close()
        raise
    write_pid_file(process, command)
    return process


def stop_server_from_pid(payload: dict[str, Any]) -> tuple[bool, str]:
    if not pid_matches_project(payload):
        return False, "PID 校验失败，为避免误杀其他程序，已拒绝停止。"
    pid = int(payload["pid"])
    result = subprocess.run(
        ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
        timeout=12,
        creationflags=CREATE_NO_WINDOW,
        check=False,
    )
    if result.returncode != 0 and get_process_info(pid):
        logging.error("taskkill failed pid=%s returncode=%s", pid, result.returncode)
        return False, "服务进程未能停止，请查看启动器日志。"
    remove_pid_file()
    return True, "服务已停止。"


class LauncherWindow:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.dpi = get_window_dpi(root)
        self.ui_scale = self.dpi / BASE_DPI
        self.ui_font = resolve_ui_font(root)
        self.root.title("Multi-Agent BI")
        self.root.geometry(
            f"{round(620 * self.ui_scale)}x{round(390 * self.ui_scale)}"
        )
        self.root.minsize(
            round(560 * self.ui_scale),
            round(350 * self.ui_scale),
        )
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)
        self.process: subprocess.Popen[Any] | None = None

        self.status_var = tk.StringVar(value="正在检查服务…")
        self.database_var = tk.StringVar(value="数据库：检查中")
        self.message_var = tk.StringVar(value="启动器已就绪")

        self._build_ui()
        self.root.after(120, self.refresh_status)
        self.root.after(4000, self._poll_status)

    def _build_ui(self) -> None:
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
                tkfont.nametofont(font_name, root=self.root).configure(
                    family=self.ui_font
                )
            except tk.TclError:
                continue

        style = ttk.Style(self.root)
        style.theme_use("vista")
        style.configure(".", font=(self.ui_font, 10))
        style.configure("Title.TLabel", font=(self.ui_font, 20, "bold"))
        style.configure("Subtitle.TLabel", foreground="#667085")
        style.configure("Status.TLabel", font=(self.ui_font, 11, "bold"))

        frame = ttk.Frame(self.root, padding=24)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Multi-Agent BI", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            frame,
            text="Production · LangGraph · Read-only PostgreSQL",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 18))

        status_box = ttk.LabelFrame(frame, text="状态", padding=14)
        status_box.pack(fill="x")
        ttk.Label(status_box, textvariable=self.status_var, style="Status.TLabel").pack(
            anchor="w"
        )
        ttk.Label(status_box, textvariable=self.database_var).pack(anchor="w", pady=(6, 0))
        ttk.Label(
            status_box,
            textvariable=self.message_var,
            style="Subtitle.TLabel",
            wraplength=round(520 * self.ui_scale),
        ).pack(anchor="w", pady=(6, 0))

        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=(18, 0))
        buttons = (
            ("启动项目", self.start_project),
            ("停止项目", self.stop_project),
            ("打开 BI Agent", lambda: webbrowser.open(APP_URL)),
            ("打开 API 文档", lambda: webbrowser.open(DOCS_URL)),
            ("健康检查", self.refresh_status),
            ("打开日志目录", self.open_logs),
        )
        for index, (label, callback) in enumerate(buttons):
            button = ttk.Button(actions, text=label, command=callback)
            button.grid(
                row=index // 3,
                column=index % 3,
                padx=(0 if index % 3 == 0 else 8, 0),
                pady=(0 if index < 3 else 8, 0),
                sticky="ew",
            )
        for column in range(3):
            actions.columnconfigure(column, weight=1)

        ttk.Label(
            frame,
            text="关闭启动器不会停止服务；只有点击“停止项目”才会终止服务。",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(18, 0))

    def _set_health(self, health: dict[str, Any] | None) -> None:
        if not health:
            self.status_var.set("服务状态：未启动")
            self.database_var.set("数据库：未连接")
            return
        database = health.get("database", {})
        ready = bool(health.get("agent_ready"))
        self.status_var.set("服务状态：已启动" + ("，Agent 已就绪" if ready else ""))
        size = database.get("size_mib")
        size_text = f"，{size} MiB" if size is not None else ""
        read_only = "只读" if database.get("read_only") else "连接模式未知"
        self.database_var.set(
            f"数据库：{database.get('status', 'unknown')}，{read_only}{size_text}"
        )

    def refresh_status(self) -> None:
        def worker() -> None:
            health = fetch_health()
            self.root.after(0, lambda: self._set_health(health))
            if health:
                self.root.after(0, lambda: self.message_var.set("健康检查通过。"))
            elif port_is_open():
                self.root.after(
                    0,
                    lambda: self.message_var.set(
                        "端口 8000 已占用，但不是当前 BI Agent 服务。"
                    ),
                )
            else:
                self.root.after(0, lambda: self.message_var.set("服务尚未启动。"))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_status(self) -> None:
        self.refresh_status()
        self.root.after(4000, self._poll_status)

    def start_project(self) -> None:
        self.message_var.set("正在启动并等待健康检查…")

        def worker() -> None:
            existing = fetch_health()
            if existing:
                self.root.after(0, lambda: self._set_health(existing))
                self.root.after(0, lambda: self.message_var.set("项目已经启动。"))
                self.root.after(0, lambda: webbrowser.open(APP_URL))
                return
            if port_is_open():
                self.root.after(
                    0,
                    lambda: self.message_var.set(
                        "启动失败：端口 8000 被其他程序占用。"
                    ),
                )
                return

            stale = read_pid_file()
            if stale and not pid_matches_project(stale):
                remove_pid_file()
            try:
                self.process = start_server_process()
            except Exception as exc:
                logging.exception("server startup failed")
                message = str(exc) or "未知错误"
                self.root.after(
                    0,
                    lambda: self.message_var.set(
                        f"启动失败：{message}。请查看启动器日志。"
                    ),
                )
                return

            deadline = time.monotonic() + START_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    self.root.after(
                        0,
                        lambda: self.message_var.set(
                            "服务进程提前退出，请查看 launcher_server.log。"
                        ),
                    )
                    return
                health = fetch_health(timeout=1.5)
                if health:
                    logging.info("server health check passed pid=%s", self.process.pid)
                    self.root.after(0, lambda: self._set_health(health))
                    self.root.after(0, lambda: self.message_var.set("启动成功。"))
                    self.root.after(0, lambda: webbrowser.open(APP_URL))
                    return
                time.sleep(0.6)

            logging.error("server startup timed out")
            self.root.after(
                0,
                lambda: self.message_var.set(
                    "启动超时，未通过健康检查。请查看服务日志。"
                ),
            )

        threading.Thread(target=worker, daemon=True).start()

    def stop_project(self) -> None:
        self.message_var.set("正在校验并停止服务…")

        def worker() -> None:
            payload = read_pid_file()
            if not payload:
                message = (
                    "没有启动器 PID 记录。为避免误杀，不能停止由其他方式启动的服务。"
                    if fetch_health()
                    else "服务当前未启动。"
                )
                self.root.after(0, lambda: self.message_var.set(message))
                return
            success, message = stop_server_from_pid(payload)
            if success:
                self.process = None
                logging.info("server stopped")
            self.root.after(0, lambda: self.message_var.set(message))
            self.root.after(0, lambda: self._set_health(fetch_health()))

        threading.Thread(target=worker, daemon=True).start()

    def open_logs(self) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(LOG_DIR)  # type: ignore[attr-defined]
        except OSError:
            messagebox.showerror("打开失败", f"无法打开日志目录：{LOG_DIR}")


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
