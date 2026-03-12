"""
config.py — loads config with sane defaults.

Follows the XDG Base Directory Specification:
  config: $XDG_CONFIG_HOME/que/config.toml  (default: ~/.config/que/config.toml)
  cache:  $XDG_DATA_HOME/que/cache.db       (default: ~/.local/share/que/cache.db)
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


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
        "mode": "move_then_music",
        # Where to move downloaded files (first existing library path is used)
        "destination": "~/Music/Music/Media.localized/Music",
    },
    "cache": {
        "path": str(CACHE_PATH),
    },
}


@dataclass
class Config:
    """Runtime configuration for que."""

    library_paths: list[Path]
    fuzzy_threshold: int
    staging_dir: Path
    audio_format: str
    use_music_app: bool
    import_mode: str
    import_destination: Path
    cache_path: Path


def _display_path(path: Path) -> str:
    """Return a user-friendly path string, preferring `~` for home-relative paths."""
    home = Path.home()
    try:
        relative = path.relative_to(home)
    except ValueError:
        return str(path)
    return "~" if not relative.parts else f"~/{relative.as_posix()}"


def render_config(config: Config) -> str:
    """Return a config object rendered as TOML."""
    library_paths = "\n".join(
        f'  "{_display_path(path)}",'
        for path in config.library_paths
    )
    return f"""# que config
# Created and edited via `que config`

[library]
paths = [
{library_paths}
]
fuzzy_threshold = {config.fuzzy_threshold}

[download]
staging_dir = "{_display_path(config.staging_dir)}"
format = "{config.audio_format}"

[import]
use_music_app = {str(config.use_music_app).lower()}
mode = "{config.import_mode}"
destination = "{_display_path(config.import_destination)}"

[cache]
path = "{_display_path(config.cache_path)}"
"""


def default_config() -> Config:
    """Return the default runtime config."""
    return Config(
        library_paths=[Path(path).expanduser() for path in _DEFAULT_LIBRARY_PATHS],
        fuzzy_threshold=_DEFAULTS["library"]["fuzzy_threshold"],
        staging_dir=Path(_DEFAULTS["download"]["staging_dir"]).expanduser(),
        audio_format=_DEFAULTS["download"]["format"],
        use_music_app=_DEFAULTS["import"]["use_music_app"],
        import_mode=_DEFAULTS["import"]["mode"],
        import_destination=Path(_DEFAULTS["import"]["destination"]).expanduser(),
        cache_path=Path(_DEFAULTS["cache"]["path"]).expanduser(),
    )


def render_default_config() -> str:
    """Return the default user config template as TOML."""
    return render_config(default_config())


def ensure_config_file(config_path: Path | None = None) -> Path:
    """Create the config file with defaults if it does not exist."""
    target_path = config_path or CONFIG_PATH
    if target_path.exists():
        return target_path

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(render_default_config(), encoding="utf-8")
    return target_path


def read_config_text(config_path: Path | None = None) -> str:
    """Return the raw config text, or the default template if missing."""
    target_path = config_path or CONFIG_PATH
    if target_path.exists():
        return target_path.read_text(encoding="utf-8")
    return render_default_config()


def write_config(config: Config, config_path: Path | None = None) -> Path:
    """Write the given config object to disk as TOML."""
    target_path = config_path or CONFIG_PATH
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(render_config(config), encoding="utf-8")
    return target_path


def load_config(config_path: Path | None = None) -> Config:
    """Load the effective config, applying defaults for missing keys."""
    raw: dict = {}
    target_path = config_path or CONFIG_PATH

    if target_path.exists():
        with open(target_path, "rb") as f:
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
    import_mode = imp.get("mode")
    if import_mode is None:
        # Reason: legacy configs used `fallback_to_folder`; the only supported
        # path now is the old folder-first strategy, so we normalize to it.
        import_mode = _DEFAULTS["import"]["mode"]

    if import_mode != "move_then_music":
        raise ValueError(
            "Unsupported import.mode. Supported values: move_then_music"
        )

    return Config(
        library_paths=library_paths,
        fuzzy_threshold=int(
            lib.get("fuzzy_threshold", _DEFAULTS["library"]["fuzzy_threshold"])
        ),
        staging_dir=Path(dl.get("staging_dir", _DEFAULTS["download"]["staging_dir"])).expanduser(),
        audio_format=dl.get("format", _DEFAULTS["download"]["format"]),
        use_music_app=bool(
            imp.get("use_music_app", _DEFAULTS["import"]["use_music_app"])
        ),
        import_mode=import_mode,
        import_destination=Path(
            imp.get("destination", _DEFAULTS["import"]["destination"])
        ).expanduser(),
        cache_path=Path(cache.get("path", _DEFAULTS["cache"]["path"])).expanduser(),
    )
