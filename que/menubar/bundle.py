"""Build metadata for the packaged `Que Menu.app` bundle."""
from __future__ import annotations


def py2app_options() -> dict[str, object]:
    """Return py2app options for the menu-bar bundle."""
    return {
        "argv_emulation": False,
        "packages": ["que"],
        "includes": [
            "que",
            "que.menubar.app",
            "que.menubar.controller",
        ],
        "iconfile": None,
        "plist": {
            "CFBundleDisplayName": "Que Menu",
            "CFBundleExecutable": "Que Menu",
            "CFBundleIdentifier": "io.tosha.que-menu",
            "CFBundleName": "Que Menu",
            "LSUIElement": True,
        },
    }
