from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DemoExperienceTests(unittest.TestCase):
    def test_demo_page_exposes_production_workspace(self) -> None:
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

        self.assertIn("Production", html)
        self.assertIn('id="database-button"', html)
        self.assertIn('id="debug-toggle"', html)
        self.assertIn('id="timeline"', html)
        self.assertIn('id="sql-panel"', html)
        self.assertIn('id="result-panel"', html)
        self.assertIn('id="detail-panel"', html)
        self.assertNotIn("setVersion", html)
        self.assertNotIn("v1 / v2", html)

    def test_frontend_has_safe_rendering_and_local_debug_switch(self) -> None:
        javascript = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("escapeHtml", javascript)
        self.assertIn("renderSafeMarkdown", javascript)
        self.assertIn("localStorage", javascript)
        self.assertIn("navigator.clipboard", javascript)
        self.assertIn("/health", javascript)
        self.assertIn("/ask", javascript)
        self.assertNotIn("innerHTML = data.final_answer", javascript)

    def test_layout_collapses_before_narrow_trace_column(self) -> None:
        css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn("grid-template-columns: minmax(0, 1.85fr) minmax(360px, 1fr)", css)
        self.assertIn("@media (max-width: 1199px)", css)
        self.assertIn("grid-template-columns: minmax(0, 1fr)", css)
        self.assertIn("position: sticky", css)

    def test_windows_launcher_uses_docker_compose_lifecycle(self) -> None:
        launcher = (ROOT / "launcher.pyw").read_text(encoding="utf-8")

        self.assertIn("CREATE_NO_WINDOW", launcher)
        self.assertIn("SetProcessDpiAwarenessContext", launcher)
        self.assertIn("SetProcessDpiAwareness", launcher)
        self.assertIn("SetProcessDPIAware", launcher)
        self.assertLess(
            launcher.index("user32.SetProcessDpiAwarenessContext"),
            launcher.index("shcore.SetProcessDpiAwareness"),
        )
        self.assertLess(
            launcher.index("shcore.SetProcessDpiAwareness"),
            launcher.index("user32.SetProcessDPIAware"),
        )
        self.assertLess(
            launcher.index("dpi_mode = enable_windows_dpi_awareness()"),
            launcher.index("root = tk.Tk()"),
        )
        self.assertIn("Microsoft YaHei UI", launcher)
        self.assertIn("Segoe UI", launcher)
        self.assertIn("fetch_health", launcher)
        self.assertIn('"info", "--format"', launcher)
        self.assertIn('"compose", *arguments', launcher)
        self.assertIn('"up", "-d"', launcher)
        self.assertIn('"up", "-d", "--build"', launcher)
        self.assertIn("Docker Desktop is not installed.", launcher)
        self.assertIn("BI_POSTGRES_OWNER_PASSWORD", launcher)
        self.assertIn("BI_POSTGRES_READONLY_PASSWORD", launcher)
        self.assertIn("data/raw/olist.zip", launcher)
        self.assertNotIn('"api.py"', launcher)
        self.assertNotIn('".venv"', launcher)
        self.assertNotIn("taskkill.exe", launcher)
        self.assertNotIn("DEEPSEEK_API_KEY", launcher)

    def test_web_ui_avoids_rasterizing_scale_and_filter_rules(self) -> None:
        css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIsNone(re.search(r"\bzoom\s*:", css, re.IGNORECASE))
        self.assertIsNone(
            re.search(r"transform\s*:\s*scale(?:3d|x|y)?\s*\(", css, re.IGNORECASE)
        )
        self.assertIsNone(re.search(r"(?:backdrop-)?filter\s*:", css, re.IGNORECASE))

    def test_packaging_and_shortcut_scripts_are_present(self) -> None:
        build_script = (ROOT / "build_launcher.cmd").read_text(encoding="utf-8")
        shortcut_script = (ROOT / "create_desktop_shortcut.ps1").read_text(
            encoding="utf-8-sig"
        )

        self.assertIn("pyinstaller", build_script.casefold())
        self.assertIn("--windowed", build_script)
        self.assertIn("MultiAgentBI-Launcher", build_script)
        self.assertIn("WScript.Shell", shortcut_script)
        self.assertIn("Multi-Agent BI.lnk", shortcut_script)

    def test_browser_acceptance_script_covers_real_interactions(self) -> None:
        script = (ROOT / "scripts" / "browser_acceptance.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("database-button", script)
        self.assertIn("debug-toggle", script)
        self.assertIn("localStorage", script)
        self.assertIn("responsiveSingleColumn", script)
        self.assertIn("Runtime.exceptionThrown", script)

    def test_dpi_preview_uses_tk_logical_dpi_without_raster_scaling(self) -> None:
        script = (ROOT / "scripts" / "launcher_dpi_preview.pyw").read_text(
            encoding="utf-8"
        )

        self.assertIn('choices=(120, 144)', script)
        self.assertIn('root.tk.call("tk", "scaling"', script)
        self.assertNotIn("Canvas", script)
        self.assertNotIn("transform", script)


if __name__ == "__main__":
    unittest.main()
