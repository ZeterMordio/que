"""Build `Que Menu.app` with py2app from a metadata-isolated temp directory."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from que.menubar.bundle import py2app_options

REPO_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = REPO_ROOT / "dist"
BUILD_DIR = REPO_ROOT / "build" / "py2app"


def main() -> None:
    """Build the app bundle into the repo-local `dist/` directory."""
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="que-menubar-build-") as tmp_dir:
        temp_root = Path(tmp_dir)
        entry_script = temp_root / "que_menu_entry.py"
        setup_script = temp_root / "setup.py"

        entry_script.write_text(
            'from que.menubar.app import main\n\nif __name__ == "__main__":\n    main()\n',
            encoding="utf-8",
        )
        setup_script.write_text(_render_setup_script(), encoding="utf-8")
        env = os.environ.copy()
        existing_pythonpath = env.get("PYTHONPATH", "")
        repo_pythonpath = str(REPO_ROOT)
        if existing_pythonpath:
            env["PYTHONPATH"] = f"{repo_pythonpath}{os.pathsep}{existing_pythonpath}"
        else:
            env["PYTHONPATH"] = repo_pythonpath

        subprocess.run(
            [
                sys.executable,
                "setup.py",
                "py2app",
                "--dist-dir",
                str(DIST_DIR),
                "--bdist-base",
                str(BUILD_DIR),
            ],
            cwd=temp_root,
            env=env,
            check=True,
        )


def _render_setup_script() -> str:
    """Return the temporary `setup.py` content for py2app."""
    return (
        "from setuptools import setup\n\n"
        "setup(\n"
        '    name="Que Menu",\n'
        '    app=["que_menu_entry.py"],\n'
        f"    options={{'py2app': {py2app_options()!r}}},\n"
        ")\n"
    )


if __name__ == "__main__":
    main()
