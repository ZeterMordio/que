"""
config_cli.py — terminal-facing config commands for que.
"""
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import termios
import tty
from pathlib import Path

from rich.console import Console
from rich.syntax import Syntax

from .config import (
    CONFIG_PATH,
    ensure_config_file,
    load_config,
    read_config_text,
    write_config,
)

console = Console()


def _resolve_editor(editor_override: str | None = None) -> list[str]:
    """Return the editor command split for subprocess execution."""
    editor = (
        editor_override
        or os.environ.get("VISUAL")
        or os.environ.get("EDITOR")
        or "vi"
    )
    return shlex.split(editor)


def _supports_wizard() -> bool:
    """Return True when stdin/stdout are interactive terminals."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def _read_key() -> str:
    """Read a single keypress from stdin, including arrow keys."""
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        first = sys.stdin.read(1)
        if first == "\x03":
            raise KeyboardInterrupt
        if first == "\r":
            return "enter"
        if first == "\x1b":
            second = sys.stdin.read(1)
            third = sys.stdin.read(1)
            if second == "[" and third == "A":
                return "up"
            if second == "[" and third == "B":
                return "down"
            return "escape"
        if first.lower() == "k":
            return "up"
        if first.lower() == "j":
            return "down"
        return first
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def _select_option(prompt: str, options: list[str], default_index: int = 0) -> str:
    """Present a small arrow-key selector and return the chosen option."""
    selected = default_index
    console.print(f"\n[bold]{prompt}[/bold]")

    while True:
        for index, option in enumerate(options):
            prefix = "›" if index == selected else " "
            style = "bold green" if index == selected else "dim"
            console.print(f"[{style}]{prefix} {option}[/{style}]")

        key = _read_key()
        if key == "up":
            selected = (selected - 1) % len(options)
        elif key == "down":
            selected = (selected + 1) % len(options)
        elif key == "enter":
            console.print(f"[dim]Selected:[/dim] {options[selected]}")
            return options[selected]
        else:
            continue

        # Reason: redraw the selector in place instead of spamming the terminal.
        sys.stdout.write(f"\x1b[{len(options)}A")
        sys.stdout.flush()


def _prompt_yes_no(prompt: str, default_yes: bool) -> bool:
    """Ask a yes/no question using the arrow-key selector."""
    options = ["Yes", "No"] if default_yes else ["No", "Yes"]
    return _select_option(prompt, options, default_index=0) == "Yes"


def _prompt_yes_no_recommended(prompt: str, default_yes: bool) -> bool:
    """Ask a yes/no question, marking the yes option as recommended."""
    yes_option = "Yes [dim](Recommended)[/dim]"
    options = [yes_option, "No"] if default_yes else ["No", yes_option]
    return _select_option(prompt, options, default_index=0).startswith("Yes")


def _prompt_text(prompt: str, default: str) -> str:
    """Ask for a text value, returning the default on blank input."""
    console.print(f"\n[bold]{prompt}[/bold]")
    console.print(f"[dim]Current:[/dim] {default}")
    value = console.input("> ").strip()
    return value or default


def _prompt_path(prompt: str, current: Path) -> Path:
    """Prompt for a path, with a quick keep-current option first."""
    if _prompt_yes_no(f"Keep current {prompt.lower()}?", default_yes=True):
        return current
    return Path(_prompt_text(prompt, str(current))).expanduser()


def _prompt_threshold(default_value: int) -> int:
    """Prompt for a fuzzy threshold value between 0 and 100."""
    if _prompt_yes_no_recommended(
        "Keep current fuzzy match threshold?",
        default_yes=True,
    ):
        return default_value

    while True:
        raw = _prompt_text("Fuzzy match threshold (0-100)", str(default_value))
        try:
            value = int(raw)
        except ValueError:
            console.print("[red]Please enter a whole number between 0 and 100.[/red]")
            continue
        if 0 <= value <= 100:
            return value
        console.print("[red]Threshold must be between 0 and 100.[/red]")


def run_config_wizard(config_path: Path | None = None) -> bool:
    """Run the interactive config wizard; return True when config was changed."""
    target_path = config_path or CONFIG_PATH
    existing = target_path.exists()
    current = load_config(target_path)

    if not _prompt_yes_no("Change any settings right now?", default_yes=False):
        console.print("[dim]No config changes made.[/dim]")
        return False

    console.print("\n[bold]Config helper[/bold]")
    console.print("[dim]Small wizard for the settings que can use today.[/dim]")
    console.print(
        "[dim]Import strategy is fixed to folder-first (`move_then_music`) for now.[/dim]"
    )

    current.use_music_app = _prompt_yes_no(
        "Use Apple Music integration for imports?",
        default_yes=current.use_music_app,
    )
    current.fuzzy_threshold = _prompt_threshold(current.fuzzy_threshold)
    current.staging_dir = _prompt_path("Download staging directory", current.staging_dir)
    current.import_destination = _prompt_path(
        "Import destination directory", current.import_destination
    )

    written_path = write_config(current, target_path)
    action = "updated" if existing else "created"
    console.print(f"\n[green]Config {action}:[/green] {written_path}")
    return True


def cmd_config(argv: list[str]) -> int:
    """Run the `que config` subcommand."""
    parser = argparse.ArgumentParser(
        prog="que config",
        description="View or edit the active que config.",
    )
    parser.add_argument(
        "--no-wizard",
        action="store_true",
        help="Show config without launching the interactive helper.",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("path", help="Print the config path.")
    subparsers.add_parser("init", help="Create the config file if missing.")
    subparsers.add_parser("wizard", help="Run the interactive config helper.")

    edit_parser = subparsers.add_parser("edit", help="Open the config in a terminal editor.")
    edit_parser.add_argument(
        "--editor",
        default=None,
        help="Override $VISUAL / $EDITOR for this invocation.",
    )

    args = parser.parse_args(argv)

    if args.command == "path":
        console.print(str(CONFIG_PATH))
        return 0

    if args.command == "init":
        target_path = ensure_config_file()
        console.print(f"[green]Config ready:[/green] {target_path}")
        return 0

    if args.command == "edit":
        target_path = ensure_config_file()
        command = _resolve_editor(args.editor)
        result = subprocess.run([*command, str(target_path)])
        return result.returncode

    if args.command == "wizard":
        if not _supports_wizard():
            console.print("[yellow]Config wizard needs an interactive terminal.[/yellow]")
            return 1
        try:
            run_config_wizard()
        except KeyboardInterrupt:
            console.print("\n[yellow]Config wizard cancelled.[/yellow]")
            return 1
        return 0

    exists = CONFIG_PATH.exists()
    console.print(f"[bold]Config path:[/bold] {CONFIG_PATH}")
    if exists:
        console.print("[dim]Showing raw config file.[/dim]")
    else:
        console.print(
            "[yellow]Config file does not exist yet.[/yellow] "
            "[dim]Showing the default template.[/dim]"
        )
    console.print(Syntax(read_config_text(), "toml", word_wrap=False))

    if args.no_wizard or not _supports_wizard():
        return 0

    try:
        run_config_wizard()
    except KeyboardInterrupt:
        console.print("\n[yellow]Config wizard cancelled.[/yellow]")
        return 1
    return 0
