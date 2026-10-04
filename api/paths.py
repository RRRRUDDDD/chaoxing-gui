"""Shared writable-location resolution for the runtime and the logger."""

import os
import sys


def data_dir() -> str:
    """Directory for state files: CHAOXING_DATA_DIR wins, then the directory
    of the frozen executable, otherwise the project root (app.py's SCRIPT_DIR)."""
    override = os.environ.get("CHAOXING_DATA_DIR")
    if override:
        return override
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
