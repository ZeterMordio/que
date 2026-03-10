"""
config.py — loads config with sane defaults.

Follows the XDG Base Directory Specification:
  config: $XDG_CONFIG_HOME/que/config.toml  (default: ~/.config/que/config.toml)
  cache:  $XDG_DATA_HOME/que/cache.db       (default: ~/.local/share/que/cache.db)
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List

# tomllib is stdlib in Python 3.11+
if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib  # pip install tomli
    except ImportError:
        tomllib = None  # type: ignore


def _xdg_config_home() -> Path:
    """Return XDG_CONFIG_HOME, defaulting to ~/.config."""
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def _xdg_data_home() -> Path:
    """Return XDG_DATA_HOME, defaulting to ~/.local/share."""
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


# Public constants — importable by other modules (e.g. a future `que config` subcommand).
CONFIG_PATH = _xdg_config_home() / "que" / "config.toml"
CACHE_PATH  = _xdg_data_home()   / "que" / "cache.db"

# Default scan paths cover both modern Apple Music and legacy iTunes layouts.
# Add your own folders (e.g. ~/Music/youtubes) in ~/.config/que/config.toml under [library] paths.
_DEFAULT_LIBRARY_PATHS = [
    "~/Music/Music/Media.localized",       # Apple Music app (macOS Catalina+)
    "~/Music/iTunes/iTunes Media",         # iTunes legacy
]

_DEFAULTS = {
    "library": {
        "paths": _DEFAULT_LIBRARY_PATHS,
        # High threshold = only skip if VERY confident track is in library.
        # Err on the side of downloading when uncertain.
        "fuzzy_threshold": 85,
    },
    "download": {
        "staging_dir": "~/Downloads/que_staging",
        "format": "m4a",
    },
    "import": {
        "use_music_app": True,
        "fallback_to_folder": True,
        # Where to move downloaded files (first existing library path is used)
        "destination": "~/Music/Music/Media.localized/Music",
    },
    "cache": {
        "path": str(CACHE_PATH),
    },
}


@dataclass
class Config:
    library_paths: List[Path]
    fuzzy_threshold: int
    staging_dir: Path
    audio_format: str
    use_music_app: bool
    fallback_to_folder: bool
    import_destination: Path
    cache_path: Path


def load_config() -> Config:
    raw: dict = {}

    if CONFIG_PATH.exists():
        if tomllib is None:
            raise RuntimeError(
                "tomllib not available. On Python <3.11, run: pip install tomli"
            )
        with open(CONFIG_PATH, "rb") as f:
            raw = tomllib.load(f)

    lib = raw.get("library", {})
    dl = raw.get("download", {})
    imp = raw.get("import", {})
    cache = raw.get("cache", {})

    # Support both `paths` (list) and legacy `path` (single string) in config
    if "paths" in lib:
        raw_paths = lib["paths"]
    elif "path" in lib:
        raw_paths = [lib["path"]]
    else:
        raw_paths = _DEFAULT_LIBRARY_PATHS

    library_paths = [Path(p).expanduser() for p in raw_paths]

    return Config(
        library_paths=library_paths,
        fuzzy_threshold=int(lib.get("fuzzy_threshold", _DEFAULTS["library"]["fuzzy_threshold"])),
        staging_dir=Path(dl.get("staging_dir", _DEFAULTS["download"]["staging_dir"])).expanduser(),
        audio_format=dl.get("format", _DEFAULTS["download"]["format"]),
        use_music_app=bool(imp.get("use_music_app", _DEFAULTS["import"]["use_music_app"])),
        fallback_to_folder=bool(imp.get("fallback_to_folder", _DEFAULTS["import"]["fallback_to_folder"])),
        import_destination=Path(imp.get("destination", _DEFAULTS["import"]["destination"])).expanduser(),
        cache_path=Path(cache.get("path", _DEFAULTS["cache"]["path"])).expanduser(),
    )
