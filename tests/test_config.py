"""Regression tests for config loading and CLI helpers."""
from __future__ import annotations

import io
from pathlib import Path

from rich.console import Console

from que import config, config_cli


def test_load_config_uses_defaults_when_file_is_missing(tmp_path, monkeypatch) -> None:
    """Missing config files should still yield a complete runtime config."""
    config_path = tmp_path / "config" / "config.toml"
    monkeypatch.setattr(config, "CONFIG_PATH", config_path)

    loaded = config.load_config()

    assert loaded.fuzzy_threshold == 85
    assert loaded.library_paths == [
        Path("~/Music/Music/Media.localized").expanduser(),
        Path("~/Music/iTunes/iTunes Media").expanduser(),
    ]
    assert loaded.cache_path == Path("~/.local/share/que/cache.db").expanduser()
    assert loaded.import_mode == "move_then_music"


def test_ensure_config_file_writes_default_template(tmp_path, monkeypatch) -> None:
    """The config helper should materialize the default template on disk."""
    config_path = tmp_path / "config" / "config.toml"
    monkeypatch.setattr(config, "CONFIG_PATH", config_path)

    written_path = config.ensure_config_file()

    assert written_path == config_path
    assert config_path.exists()
    assert "paths = [" in config_path.read_text(encoding="utf-8")


def test_cmd_config_shows_default_template_when_file_is_missing(
    tmp_path, monkeypatch
) -> None:
    """`que config` should be useful even before the file exists."""
    config_path = tmp_path / "config" / "config.toml"
    output = io.StringIO()

    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(config_cli, "CONFIG_PATH", config_path)
    monkeypatch.setattr(
        config_cli,
        "console",
        Console(file=output, force_terminal=False, color_system=None),
    )

    result = config_cli.cmd_config([])

    assert result == 0
    rendered = output.getvalue()
    assert "Config file does not exist yet." in rendered
    assert "[library]" in rendered


def test_cmd_config_init_creates_config_file(tmp_path, monkeypatch) -> None:
    """`que config init` should create the user config file."""
    config_path = tmp_path / "config" / "config.toml"
    output = io.StringIO()

    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(config_cli, "CONFIG_PATH", config_path)
    monkeypatch.setattr(
        config_cli,
        "console",
        Console(file=output, force_terminal=False, color_system=None),
    )

    result = config_cli.cmd_config(["init"])

    assert result == 0
    assert config_path.exists()
    assert "Config ready:" in output.getvalue()


def test_cmd_config_edit_uses_editor_override(tmp_path, monkeypatch) -> None:
    """`que config edit` should open the materialized config file in the editor."""
    config_path = tmp_path / "config" / "config.toml"
    called: dict[str, list[str]] = {}

    def fake_run(command: list[str]) -> object:
        called["command"] = command
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(config_cli, "CONFIG_PATH", config_path)
    monkeypatch.setattr(config_cli.subprocess, "run", fake_run)

    result = config_cli.cmd_config(["edit", "--editor", "vim -u NONE"])

    assert result == 0
    assert config_path.exists()
    assert called["command"] == ["vim", "-u", "NONE", str(config_path)]


def test_cmd_config_no_wizard_skips_interactive_helper(
    tmp_path, monkeypatch
) -> None:
    """`que config --no-wizard` should not invoke the wizard."""
    config_path = tmp_path / "config" / "config.toml"
    output = io.StringIO()

    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(config_cli, "CONFIG_PATH", config_path)
    monkeypatch.setattr(
        config_cli,
        "console",
        Console(file=output, force_terminal=False, color_system=None),
    )
    monkeypatch.setattr(config_cli, "_supports_wizard", lambda: True)
    monkeypatch.setattr(
        config_cli,
        "run_config_wizard",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("wizard ran")),
    )

    result = config_cli.cmd_config(["--no-wizard"])

    assert result == 0
    assert "[library]" in output.getvalue()


def test_run_config_wizard_stops_when_user_says_no(
    tmp_path, monkeypatch
) -> None:
    """The wizard should exit cleanly without creating a config on 'No'."""
    config_path = tmp_path / "config" / "config.toml"
    output = io.StringIO()

    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(config_cli, "CONFIG_PATH", config_path)
    monkeypatch.setattr(
        config_cli,
        "console",
        Console(file=output, force_terminal=False, color_system=None),
    )
    monkeypatch.setattr(config_cli, "_prompt_yes_no", lambda *args, **kwargs: False)

    changed = config_cli.run_config_wizard()

    assert changed is False
    assert not config_path.exists()
    assert "No config changes made." in output.getvalue()


def test_run_config_wizard_updates_config_values(tmp_path, monkeypatch) -> None:
    """The wizard should persist the edited values back to the config file."""
    config_path = tmp_path / "config" / "config.toml"
    output = io.StringIO()
    yes_no_answers = iter([True, False, False, False, False])
    text_answers = iter(["72", str(tmp_path / "staging"), str(tmp_path / "library")])

    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    monkeypatch.setattr(config_cli, "CONFIG_PATH", config_path)
    monkeypatch.setattr(
        config_cli,
        "console",
        Console(file=output, force_terminal=False, color_system=None),
    )
    monkeypatch.setattr(
        config_cli,
        "_prompt_yes_no",
        lambda *args, **kwargs: next(yes_no_answers),
    )
    monkeypatch.setattr(
        config_cli,
        "_prompt_threshold",
        lambda current: int(next(text_answers)),
    )
    monkeypatch.setattr(
        config_cli,
        "_prompt_text",
        lambda prompt, default: next(text_answers),
    )

    changed = config_cli.run_config_wizard()
    loaded = config.load_config(config_path)

    assert changed is True
    assert loaded.fuzzy_threshold == 72
    assert loaded.use_music_app is False
    assert loaded.import_mode == "move_then_music"
    assert loaded.staging_dir == tmp_path / "staging"
    assert loaded.import_destination == tmp_path / "library"
    assert "Config created:" in output.getvalue()


def test_load_config_normalizes_legacy_fallback_to_folder_key(
    tmp_path, monkeypatch
) -> None:
    """Legacy `fallback_to_folder` configs should normalize to the supported mode."""
    config_path = tmp_path / "config" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        """
[import]
fallback_to_folder = false
use_music_app = true
destination = "~/Music/Library"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "CONFIG_PATH", config_path)

    loaded = config.load_config()

    assert loaded.import_mode == "move_then_music"
