#!/bin/bash
# PhoneDriver Installer
# Usage: curl -sL https://raw.githubusercontent.com/mohitsoni48/phone-driver/main/install.sh | bash
#   or:  ./install.sh

set -euo pipefail

INSTALL_DIR="$HOME/.claude/phonedriver"
COMMANDS_DIR="$HOME/.claude/commands"
REPO_URL="https://raw.githubusercontent.com/mohitsoni48/phone-driver/main"

echo "╔══════════════════════════════════════╗"
echo "║    PhoneDriver Installer             ║"
echo "║    AI-powered Android automation     ║"
echo "╚══════════════════════════════════════╝"
echo ""

# Check prerequisites
check_prereqs() {
    local missing=()

    if ! command -v adb &>/dev/null; then
        if [ -x "$HOME/Library/Android/sdk/platform-tools/adb" ]; then
            echo "[ok] ADB found at ~/Library/Android/sdk/platform-tools/adb"
        else
            missing+=("adb")
        fi
    else
        echo "[ok] ADB found at $(command -v adb)"
    fi

    if ! command -v python3 &>/dev/null; then
        missing+=("python3")
    else
        echo "[ok] python3 found"
    fi

    if [ ${#missing[@]} -gt 0 ]; then
        echo ""
        echo "Missing prerequisites: ${missing[*]}"
        echo ""
        if [[ " ${missing[*]} " =~ " adb " ]]; then
            echo "Install ADB:"
            echo "  macOS:  brew install android-platform-tools"
            echo "  Linux:  sudo apt install adb"
            echo "  Or:     https://developer.android.com/tools/releases/platform-tools"
        fi
        if [[ " ${missing[*]} " =~ " python3 " ]]; then
            echo "Install Python 3: https://python.org"
        fi
        exit 1
    fi
}

install_files() {
    echo ""
    echo "Installing to $INSTALL_DIR ..."
    mkdir -p "$INSTALL_DIR/scripts"
    mkdir -p "$COMMANDS_DIR"

    # If running from the repo, copy local files
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || SCRIPT_DIR=""

    if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/scripts/adb-helpers.sh" ]; then
        echo "  Installing from local repo: $SCRIPT_DIR"
        cp "$SCRIPT_DIR/scripts/adb-helpers.sh" "$INSTALL_DIR/scripts/"
        cp "$SCRIPT_DIR/scripts/memory-tree.py" "$INSTALL_DIR/scripts/"
        cp "$SCRIPT_DIR/scripts/pd" "$INSTALL_DIR/scripts/" 2>/dev/null || true
        cp "$SCRIPT_DIR/.claude/commands/phone-driver.md" "$COMMANDS_DIR/phone-driver.md"
        # Copy memory only if not already present (don't overwrite learned skills)
        if [ ! -f "$INSTALL_DIR/memory.json" ]; then
            if [ -f "$SCRIPT_DIR/.claude/commands/phonedriver-memory.json" ]; then
                cp "$SCRIPT_DIR/.claude/commands/phonedriver-memory.json" "$INSTALL_DIR/memory.json"
            fi
        fi
    else
        echo "  Downloading from GitHub..."
        curl -sL "$REPO_URL/scripts/adb-helpers.sh" -o "$INSTALL_DIR/scripts/adb-helpers.sh"
        curl -sL "$REPO_URL/scripts/memory-tree.py" -o "$INSTALL_DIR/scripts/memory-tree.py"
        curl -sL "$REPO_URL/.claude/commands/phone-driver.md" -o "$COMMANDS_DIR/phone-driver.md"
        if [ ! -f "$INSTALL_DIR/memory.json" ]; then
            curl -sL "$REPO_URL/.claude/commands/phonedriver-memory.json" -o "$INSTALL_DIR/memory.json"
        fi
    fi

    chmod +x "$INSTALL_DIR/scripts/adb-helpers.sh"
    chmod +x "$INSTALL_DIR/scripts/memory-tree.py"
    chmod +x "$INSTALL_DIR/scripts/pd" 2>/dev/null || true

    echo "  [ok] Scripts installed"
    echo "  [ok] Command installed"
    if [ -f "$INSTALL_DIR/memory.json" ]; then
        echo "  [ok] Memory initialized (existing memory preserved)"
    fi
}

configure_permissions() {
    local settings_file="$HOME/.claude/settings.json"

    echo ""
    echo "Configuring permissions..."

    # Check if settings file exists and has permissions
    if [ -f "$settings_file" ]; then
        if python3 -c "
import json, sys
with open('$settings_file') as f: data = json.load(f)
perms = data.get('permissions', {}).get('allow', [])
needed = 'Bash(\$HOME/.claude/phonedriver/scripts/adb-helpers.sh *)'
if needed in perms:
    sys.exit(0)
else:
    sys.exit(1)
" 2>/dev/null; then
            echo "  [ok] Permissions already configured"
            return
        fi
    fi

    echo ""
    echo "PhoneDriver needs these permissions to run without prompts:"
    echo '  Bash($HOME/.claude/phonedriver/scripts/adb-helpers.sh *)'
    echo '  Read(/tmp/phonedriver_*)'
    echo ""
    echo "Add them to ~/.claude/settings.json under permissions.allow,"
    echo "or approve them when prompted during first use."
}

print_success() {
    echo ""
    echo "════════════════════════════════════════"
    echo "  PhoneDriver installed successfully!"
    echo "════════════════════════════════════════"
    echo ""
    echo "Usage (in Claude Code):"
    echo '  /phone-driver "open Chrome and search for weather"'
    echo '  /phone-driver "open Settings and enable WiFi"'
    echo ""
    echo "Prerequisites:"
    echo "  - Android device connected via USB"
    echo "  - USB debugging enabled on the device"
    echo "  - Run 'adb devices' to verify connection"
    echo ""
    echo "Files installed:"
    echo "  $COMMANDS_DIR/phone-driver.md"
    echo "  $INSTALL_DIR/scripts/adb-helpers.sh"
    echo "  $INSTALL_DIR/scripts/memory-tree.py"
    echo "  $INSTALL_DIR/memory.json"
    echo ""
}

# Run
check_prereqs
install_files
configure_permissions
print_success
