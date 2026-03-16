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


def test_apply_config_update_merges_partial_payload(tmp_path) -> None:
    """Partial config updates should merge onto the existing runtime config."""
    base = config.default_config()
    updated = config.apply_config_update(
        {
            "use_music_app": False,
            "fuzzy_threshold": 72,
            "staging_dir": str(tmp_path / "staging"),
            "import_destination": str(tmp_path / "library"),
        },
        base_config=base,
    )

    assert updated.use_music_app is False
    assert updated.fuzzy_threshold == 72
    assert updated.staging_dir == tmp_path / "staging"
    assert updated.import_destination == tmp_path / "library"
    assert updated.cache_path == base.cache_path


def test_apply_config_update_rejects_invalid_threshold() -> None:
    """Config updates should validate the fuzzy threshold range."""
    try:
        config.apply_config_update({"fuzzy_threshold": 120}, base_config=config.default_config())
    except ValueError as exc:
        assert "fuzzy_threshold" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_config_to_dict_includes_config_path(tmp_path) -> None:
    """Serialized config payloads should include the backing config path."""
    payload = config.config_to_dict(config.default_config(), tmp_path / "config.toml")

    assert payload["config_path"] == str(tmp_path / "config.toml")
    assert payload["audio_format"] == "m4a"


def test_write_config_preserves_unknown_sections_and_keys(tmp_path) -> None:
    """Config writes should not delete raw TOML fields outside the typed model."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[library]
paths = ["~/Music/Custom"]
fuzzy_threshold = 80
custom_match_mode = "album"

[download]
staging_dir = "~/Downloads/old"
format = "mp3"
retries = 4

[import]
use_music_app = true
mode = "move_then_music"
destination = "~/Music/Library"

[cache]
path = "~/Library/Caches/que.db"

[menubar]
quick_sync_playlist = "Road Trip"
""".strip(),
        encoding="utf-8",
    )

    updated = config.apply_config_update(
        {
            "fuzzy_threshold": 72,
            "staging_dir": str(tmp_path / "staging"),
        },
        base_config=config.load_config(config_path),
    )
    config.write_config(updated, config_path)
    written = config_path.read_text(encoding="utf-8")

    assert 'custom_match_mode = "album"' in written
    assert "retries = 4" in written
    assert "[menubar]" in written
    assert 'quick_sync_playlist = "Road Trip"' in written
    assert "fuzzy_threshold = 72" in written
