"""
config.py — loads config with sane defaults.

Follows the XDG Base Directory Specification:
  config: $XDG_CONFIG_HOME/que/config.toml  (default: ~/.config/que/config.toml)
  cache:  $XDG_DATA_HOME/que/cache.db       (default: ~/.local/share/que/cache.db)
"""
from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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


def config_to_dict(config: Config, config_path: Path | None = None) -> dict[str, Any]:
    """Serialize runtime config for UI and daemon responses.

    Args:
        config: Runtime config object.
        config_path: Explicit config path when different from the default.

    Returns:
        JSON-safe config payload.
    """
    return {
        "config_path": str(config_path or CONFIG_PATH),
        "library_paths": [str(path) for path in config.library_paths],
        "fuzzy_threshold": config.fuzzy_threshold,
        "staging_dir": str(config.staging_dir),
        "audio_format": config.audio_format,
        "use_music_app": config.use_music_app,
        "import_mode": config.import_mode,
        "import_destination": str(config.import_destination),
        "cache_path": str(config.cache_path),
    }


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


def apply_config_update(
    payload: Mapping[str, Any],
    *,
    base_config: Config | None = None,
) -> Config:
    """Merge a UI/API update payload into config and validate it.

    Args:
        payload: Partial config payload from a client.
        base_config: Existing config to merge onto. Defaults to the active config.

    Returns:
        Validated runtime config.

    Raises:
        ValueError: When a field is invalid.
    """
    current = base_config or load_config()
    merged: dict[str, Any] = {
        "library": {
            "paths": [str(path) for path in current.library_paths],
            "fuzzy_threshold": current.fuzzy_threshold,
        },
        "download": {"staging_dir": str(current.staging_dir), "format": current.audio_format},
        "import": {
            "use_music_app": current.use_music_app,
            "mode": current.import_mode,
            "destination": str(current.import_destination),
        },
        "cache": {"path": str(current.cache_path)},
    }

    if "library_paths" in payload:
        merged["library"]["paths"] = payload["library_paths"]
    if "fuzzy_threshold" in payload:
        merged["library"]["fuzzy_threshold"] = payload["fuzzy_threshold"]
    if "staging_dir" in payload:
        merged["download"]["staging_dir"] = payload["staging_dir"]
    if "audio_format" in payload:
        merged["download"]["format"] = payload["audio_format"]
    if "use_music_app" in payload:
        merged["import"]["use_music_app"] = payload["use_music_app"]
    if "import_mode" in payload:
        merged["import"]["mode"] = payload["import_mode"]
    if "import_destination" in payload:
        merged["import"]["destination"] = payload["import_destination"]
    if "cache_path" in payload:
        merged["cache"]["path"] = payload["cache_path"]

    return _coerce_config_sections(merged)


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
    if target_path.exists():
        raw = _read_raw_config(target_path)
        target_path.write_text(
            _render_toml_mapping(_merge_config_sections(raw, config)),
            encoding="utf-8",
        )
    else:
        target_path.write_text(render_config(config), encoding="utf-8")
    return target_path


def load_config(config_path: Path | None = None) -> Config:
    """Load the effective config, applying defaults for missing keys."""
    target_path = config_path or CONFIG_PATH
    return _coerce_config_sections(_read_raw_config(target_path))


def _coerce_config_sections(raw: Mapping[str, Any]) -> Config:
    """Coerce raw config sections into a validated runtime config."""
    lib = _coerce_section(raw.get("library"), "library")
    dl = _coerce_section(raw.get("download"), "download")
    imp = _coerce_section(raw.get("import"), "import")
    cache = _coerce_section(raw.get("cache"), "cache")

    if "paths" in lib:
        raw_paths = lib["paths"]
    elif "path" in lib:
        raw_paths = [lib["path"]]
    else:
        raw_paths = _DEFAULT_LIBRARY_PATHS
    if not isinstance(raw_paths, list):
        raise ValueError("library.paths must be a list of path strings")
    library_paths = [_parse_path_value("library.paths", path) for path in raw_paths]

    fuzzy_threshold = int(lib.get("fuzzy_threshold", _DEFAULTS["library"]["fuzzy_threshold"]))
    if not 0 <= fuzzy_threshold <= 100:
        raise ValueError("library.fuzzy_threshold must be between 0 and 100")

    import_mode = imp.get("mode")
    if import_mode is None:
        # Reason: legacy configs used `fallback_to_folder`; the only supported
        # path now is the old folder-first strategy, so we normalize to it.
        import_mode = _DEFAULTS["import"]["mode"]
    import_mode = str(import_mode)
    if import_mode != "move_then_music":
        raise ValueError("Unsupported import.mode. Supported values: move_then_music")

    audio_format = str(dl.get("format", _DEFAULTS["download"]["format"])).strip()
    if not audio_format:
        raise ValueError("download.format must not be empty")

    return Config(
        library_paths=library_paths,
        fuzzy_threshold=fuzzy_threshold,
        staging_dir=_parse_path_value(
            "download.staging_dir",
            dl.get("staging_dir", _DEFAULTS["download"]["staging_dir"]),
        ),
        audio_format=audio_format,
        use_music_app=bool(imp.get("use_music_app", _DEFAULTS["import"]["use_music_app"])),
        import_mode=import_mode,
        import_destination=_parse_path_value(
            "import.destination",
            imp.get("destination", _DEFAULTS["import"]["destination"]),
        ),
        cache_path=_parse_path_value("cache.path", cache.get("path", _DEFAULTS["cache"]["path"])),
    )


def _coerce_section(value: Any, section_name: str) -> Mapping[str, Any]:
    """Return one config section as a mapping."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{section_name} must be a table/object")
    return value


def _parse_path_value(field_name: str, value: Any) -> Path:
    """Parse one config path-like value."""
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field_name} must not be empty")
    return Path(text).expanduser()


def _read_raw_config(config_path: Path) -> dict[str, Any]:
    """Return raw TOML data from one config path."""
    if not config_path.exists():
        return {}
    with open(config_path, "rb") as f:
        raw = tomllib.load(f)
    if not isinstance(raw, dict):
        raise ValueError("config root must be a table/object")
    return raw


def _merge_config_sections(raw: Mapping[str, Any], config: Config) -> dict[str, Any]:
    """Merge runtime config values into raw TOML data without dropping extras."""
    merged = dict(raw)
    rendered = {
        "library": {
            **_coerce_section(raw.get("library"), "library"),
            "paths": [_display_path(path) for path in config.library_paths],
            "fuzzy_threshold": config.fuzzy_threshold,
        },
        "download": {
            **_coerce_section(raw.get("download"), "download"),
            "staging_dir": _display_path(config.staging_dir),
            "format": config.audio_format,
        },
        "import": {
            **_coerce_section(raw.get("import"), "import"),
            "use_music_app": config.use_music_app,
            "mode": config.import_mode,
            "destination": _display_path(config.import_destination),
        },
        "cache": {
            **_coerce_section(raw.get("cache"), "cache"),
            "path": _display_path(config.cache_path),
        },
    }
    merged.update(rendered)
    return merged


def _render_toml_mapping(data: Mapping[str, Any]) -> str:
    """Render a TOML mapping with nested tables."""
    lines: list[str] = []
    _append_toml_table(lines, data, prefix=None)
    return "\n".join(lines).rstrip() + "\n"


def _append_toml_table(
    lines: list[str],
    table: Mapping[str, Any],
    *,
    prefix: str | None,
) -> None:
    """Append one TOML table plus nested child tables."""
    scalar_items: list[tuple[str, Any]] = []
    table_items: list[tuple[str, Mapping[str, Any]]] = []
    for key, value in table.items():
        if isinstance(value, Mapping):
            table_items.append((key, value))
        else:
            scalar_items.append((key, value))

    if prefix is not None:
        lines.append(f"[{prefix}]")

    for key, value in scalar_items:
        lines.append(f"{key} = {_toml_value(value)}")

    if prefix is not None and table_items:
        lines.append("")

    for index, (key, value) in enumerate(table_items):
        child_prefix = f"{prefix}.{key}" if prefix else key
        _append_toml_table(lines, value, prefix=child_prefix)
        if index != len(table_items) - 1:
            lines.append("")


def _toml_value(value: Any) -> str:
    """Serialize one scalar or list TOML value."""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise TypeError(f"Unsupported TOML value: {value!r}")
