"""Open the launcher UI at a logical DPI for layout verification only."""

from __future__ import annotations

import argparse
import runpy
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dpi", type=int, choices=(120, 144), required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    launcher: dict[str, Any] = runpy.run_path(str(PROJECT_ROOT / "launcher.pyw"))
    launcher["enable_windows_dpi_awareness"]()
    root = launcher["tk"].Tk()
    root.tk.call("tk", "scaling", args.dpi / 72.0)
    window_class = launcher["LauncherWindow"]
    window_class.__init__.__globals__["get_window_dpi"] = lambda _root: args.dpi
    window_class(root, auto_start=False)
    root.title(f"Multi-Agent BI - logical {round(args.dpi * 100 / 96)}% DPI preview")
    root.mainloop()


if __name__ == "__main__":
    main()
