#!/usr/bin/env bash
# install.sh — installs `que` and its dependencies on macOS
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Checking dependencies..."

# uv
if ! command -v uv &>/dev/null; then
    echo "    Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Reload PATH so uv is available immediately
    export PATH="$HOME/.cargo/bin:$PATH"
fi

# yt-dlp
if ! command -v yt-dlp &>/dev/null; then
    echo "    Installing yt-dlp..."
    brew install yt-dlp 2>/dev/null || uv tool install yt-dlp
fi

echo "==> Installing que..."
uv tool install "$SCRIPT_DIR"

echo ""
echo "✓ que installed successfully."
echo ""
echo "Usage:"
echo "  que                    — read clipboard and sync playlist"
echo "  que <URL>              — process a single URL"
echo "  que --dry-run          — preview without downloading"
echo "  que list               — show history"
echo "  que --help             — full help"
echo ""
echo "Config: ~/.config/que/config.toml (created on first run with defaults)"
echo "Cache:  ~/.local/share/que/cache.db"
