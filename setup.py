"""Compatibility setup shim for setuptools and py2app helpers."""
from __future__ import annotations

import subprocess
import sys

from setuptools import setup

if __name__ == "__main__" and "py2app" in sys.argv:
    raise SystemExit(
        subprocess.call([sys.executable, "scripts/build_menubar_app.py"])
    )

setup()
