#!/usr/bin/env python3
"""
IgorVision – Professional Image Quality Inspector
==================================================

Bokeh-aware image quality checker inspired by Agisoft Metashape's
"check image quality" feature.

Usage
-----
    python main.py
"""
from __future__ import annotations

import logging
import os
import sys

# Ensure the project root is on sys.path so bare imports work
# regardless of the caller's CWD.
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

__version__ = "5.0.0"


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )

    # Lazy UI imports: analysis worker processes re-import this module on
    # Windows (spawn) – keeping PyQt out of module scope keeps them lean.
    from PyQt5.QtWidgets import QApplication
    from ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("IgorVision")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("IgorVision")
    app.setStyle("Fusion")

    window = MainWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
