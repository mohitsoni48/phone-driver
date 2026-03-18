#!/bin/bash
# PhoneDriver Installer
# Usage: curl -sL https://raw.githubusercontent.com/mohitsoni48/phone-driver/main/install.sh | bash
#   or:  git clone https://github.com/mohitsoni48/phone-driver.git && cd phone-driver && ./install.sh

set -euo pipefail

INSTALL_DIR="$HOME/.claude/phonedriver"
COMMANDS_DIR="$HOME/.claude/commands"
REPO="mohitsoni48/phone-driver"
REPO_URL="https://raw.githubusercontent.com/$REPO/main"

echo "╔══════════════════════════════════════╗"
echo "║       PhoneDriver Installer          ║"
echo "║   AI-powered Android automation      ║"
echo "╚══════════════════════════════════════╝"
echo ""

# ── Prerequisites ──────────────────────────────────────────────────────

missing=()

if command -v adb &>/dev/null; then
    echo "[ok] ADB: $(command -v adb)"
elif [ -x "$HOME/Library/Android/sdk/platform-tools/adb" ]; then
    echo "[ok] ADB: ~/Library/Android/sdk/platform-tools/adb"
elif [ -x "/usr/local/bin/adb" ]; then
    echo "[ok] ADB: /usr/local/bin/adb"
else
    missing+=("adb")
fi

if command -v python3 &>/dev/null; then
    echo "[ok] python3: $(python3 --version 2>&1)"
else
    missing+=("python3")
fi

if [ ${#missing[@]} -gt 0 ]; then
    echo ""
    echo "MISSING: ${missing[*]}"
    [[ " ${missing[*]} " =~ " adb " ]] && echo "  Install ADB: brew install android-platform-tools (macOS) or sudo apt install adb (Linux)"
    [[ " ${missing[*]} " =~ " python3 " ]] && echo "  Install Python 3: https://python.org"
    exit 1
fi

# ── Install ────────────────────────────────────────────────────────────

echo ""
mkdir -p "$INSTALL_DIR/scripts"
mkdir -p "$COMMANDS_DIR"

# Detect: running from cloned repo or piped from curl?
SCRIPT_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || true
fi

FILES_OK=true

if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/scripts/adb-helpers.sh" ]; then
    echo "Installing from local clone..."
    cp "$SCRIPT_DIR/scripts/adb-helpers.sh"  "$INSTALL_DIR/scripts/adb-helpers.sh"
    cp "$SCRIPT_DIR/scripts/memory-tree.py"  "$INSTALL_DIR/scripts/memory-tree.py"
    cp "$SCRIPT_DIR/scripts/pd"              "$INSTALL_DIR/scripts/pd"
    # Command prompt
    if [ -f "$SCRIPT_DIR/commands/phone-driver.md" ]; then
        cp "$SCRIPT_DIR/commands/phone-driver.md" "$COMMANDS_DIR/phone-driver.md"
    fi
    # Seed memory (don't overwrite existing)
    if [ ! -f "$INSTALL_DIR/memory.json" ]; then
        if [ -f "$SCRIPT_DIR/memory-seed.json" ]; then
            cp "$SCRIPT_DIR/memory-seed.json" "$INSTALL_DIR/memory.json"
        fi
    fi
else
    echo "Downloading from GitHub..."
    for f in scripts/adb-helpers.sh scripts/memory-tree.py; do
        if ! curl -sfL "$REPO_URL/$f" -o "$INSTALL_DIR/$f"; then
            echo "ERROR: Failed to download $f"
            FILES_OK=false
        fi
    done
    if ! curl -sfL "$REPO_URL/commands/phone-driver.md" -o "$COMMANDS_DIR/phone-driver.md" 2>/dev/null; then
        echo "ERROR: Failed to download phone-driver.md"
        FILES_OK=false
    fi
    if ! curl -sfL "$REPO_URL/scripts/pd" -o "$INSTALL_DIR/scripts/pd" 2>/dev/null; then
        echo "ERROR: Failed to download pd wrapper"
        FILES_OK=false
    fi
    # Seed memory
    if [ ! -f "$INSTALL_DIR/memory.json" ]; then
        curl -sfL "$REPO_URL/memory-seed.json" -o "$INSTALL_DIR/memory.json" 2>/dev/null || \
        echo '{"schema_version":2,"devices":{},"apps":{},"tasks":{},"settings_paths":{}}' > "$INSTALL_DIR/memory.json"
    fi
fi

if [ "$FILES_OK" = false ]; then
    echo ""
    echo "Some files failed to download. Try cloning instead:"
    echo "  git clone https://github.com/$REPO.git && cd phone-driver && ./install.sh"
    exit 1
fi

# Make executable
chmod +x "$INSTALL_DIR/scripts/adb-helpers.sh"
chmod +x "$INSTALL_DIR/scripts/memory-tree.py"
chmod +x "$INSTALL_DIR/scripts/pd"

echo ""
echo "[ok] Scripts installed"
echo "[ok] Command installed"
[ -f "$INSTALL_DIR/memory.json" ] && echo "[ok] Memory ready (existing skills preserved)"

# ── Verify ─────────────────────────────────────────────────────────────

echo ""
echo "Verifying..."
if bash "$INSTALL_DIR/scripts/adb-helpers.sh" help > /dev/null 2>&1; then
    echo "[ok] adb-helpers.sh runs correctly"
else
    echo "[!!] adb-helpers.sh failed — check bash is available"
fi

if "$INSTALL_DIR/pd" help > /dev/null 2>&1; then
    echo "[ok] pd runner works"
else
    echo "[!!] pd runner failed"
fi

# ── Done ───────────────────────────────────────────────────────────────

echo ""
echo "════════════════════════════════════════"
echo "  PhoneDriver installed!"
echo "════════════════════════════════════════"
echo ""
echo "Usage (in Claude Code):"
echo '  /phone-driver "open Chrome and search for weather"'
echo '  /phone-driver "open Settings and enable WiFi"'
echo ""
echo "Files:"
echo "  $COMMANDS_DIR/phone-driver.md       ← command"
echo "  $INSTALL_DIR/pd                     ← runner"
echo "  $INSTALL_DIR/scripts/               ← scripts"
echo "  $INSTALL_DIR/memory.json            ← learned skills"
echo ""
echo "Tip: First run will ask for permission to execute ADB commands."
echo "     Approve once and it remembers."
echo ""
