# SPDX-License-Identifier: MIT
"""Frozen-app entry point for PyInstaller.

Uses an absolute import so it works as the top-level script the bootloader runs
(screen_annotator/__main__.py can't be the entry: its relative `from .app`
import has no parent package when run as __main__)."""

from screen_annotator.app import main

if __name__ == "__main__":
    main()
